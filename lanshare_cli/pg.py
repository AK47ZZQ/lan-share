"""PostgreSQL 的安装、初始化、启停与数据目录迁移。

设计前提（2026-10-31 主人拍板）：
    **以后只用 PostgreSQL 一个数据库**，不再在 SQLite / PG 之间切换。
    安装器默认装 PG，生产也只跑 PG。SQLite 分支后续会从 db.py 里摘掉。

为什么自己管 PG 而不是让用户单独装：
    安装器要做的是「双击 → 能用」。让用户先去下 PostgreSQL 安装包、
    设密码、建库、配 pg_hba，等于把开箱即用变成一道配置题。
    所以安装包直接带一份精简过的 PG 二进制，装的时候自己 initdb。

数据目录可迁移（很多人不想放 C 盘）：
    所有路径都从 data_dir 推导，迁移时整体搬走再改注册表即可。
    详见 move_data_dir()。

目录布局（data_dir 由用户在安装时选，可以是 D 盘）：
    <data_dir>\
        pgdata\      PostgreSQL 数据（initdb 产物）
        data\        网盘用户文件
        logs\        日志
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import string
import subprocess
from pathlib import Path

from .service import NssmService

# 默认端口。选 5433 而不是 5432，避免和用户自己装的 PostgreSQL 撞车
# （家里装过 PG 的人不少，5432 冲突会让人一头雾水）。
DEFAULT_PORT = 5433

DEFAULT_DB = "lanshare"
DEFAULT_USER = "lanshare"

# PG 服务名后缀，和服务前缀拼成 <prefix>-postgresql
SERVICE_SUFFIX = "-postgresql"


class PostgresError(RuntimeError):
    """PG 相关的可预期失败，调用方捕获后转成人话。"""


class Postgres:
    """一份随程序走的 PostgreSQL。"""

    def __init__(
        self,
        root: str | Path,
        data_dir: str | Path,
        *,
        prefix: str = "lanshare",
        port: int = DEFAULT_PORT,
        db_name: str = DEFAULT_DB,
        db_user: str = DEFAULT_USER,
        password: str = "",
    ):
        self.root = Path(root)
        self.data_dir = Path(data_dir)
        self.prefix = prefix
        self.port = port
        self.db_name = db_name
        self.db_user = db_user
        self.password = password

    # ── 路径 ──────────────────────────────────────────────

    @property
    def bin_dir(self) -> Path:
        """PG 二进制目录。安装版在 <root>/pgsql/bin。"""
        return self.root / "pgsql" / "bin"

    @property
    def pgdata(self) -> Path:
        """PG 数据目录。跟着 data_dir 走，这样迁移数据目录时一起搬。"""
        return self.data_dir / "pgdata"

    @property
    def service_name(self) -> str:
        return self.prefix + SERVICE_SUFFIX

    def exe(self, name: str) -> Path:
        """取一个 PG 可执行文件的路径，不存在就报错。"""
        p = self.bin_dir / (name + ".exe")
        if not p.exists():
            raise PostgresError(
                f"找不到 {p}。安装包里应该带 PG 二进制，"
                f"如果你在开发环境跑，请确认 {self.bin_dir} 存在。"
            )
        return p

    # ── 状态 ──────────────────────────────────────────────

    def is_initialized(self) -> bool:
        """data 目录里有没有 PG_VERSION —— 这是 initdb 跑过的标志。"""
        return (self.pgdata / "PG_VERSION").exists()

    def installed_version(self) -> str:
        try:
            return (self.pgdata / "PG_VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def connection_url(self) -> str:
        """给 app 用的 DATABASE_URL。密码里可能带特殊字符，要做 URL 编码。"""
        from urllib.parse import quote

        pwd = quote(self.password, safe="")
        return (
            f"postgresql://{self.db_user}:{pwd}"
            f"@127.0.0.1:{self.port}/{self.db_name}"
        )

    # ── 生命周期 ──────────────────────────────────────────

    def initdb(self, *, encoding: str = "UTF8") -> None:
        """初始化数据目录。

        --locale=C + --encoding=UTF8 是最省事的组合：
          * 不依赖 Windows 的本地化设置（中文系统上 locale 常常被配成
            Chinese_China.936，那是 GBK，存中文会出乱码）
          * 排序规则是确定性的，跨机器行为一致

        --auth=scram-sha-256 + --pwfile 给超级用户设密码。
        密码从 stdin 文件传，不放在命令行里（命令行会被其他进程看到）。
        """
        if self.is_initialized():
            return

        if not self.password:
            raise PostgresError("initdb 需要超级用户密码")

        self.pgdata.parent.mkdir(parents=True, exist_ok=True)

        pwfile = self.pgdata.parent / ".pgpass.tmp"
        try:
            pwfile.write_text(self.password, encoding="utf-8")
            cmd = [
                str(self.exe("initdb")),
                "-D", str(self.pgdata),
                "-U", "postgres",
                "--pwfile", str(pwfile),
                "--auth=scram-sha-256",
                "--encoding", encoding,
                "--locale=C",
            ]
            res = self._run(cmd, timeout=180)
            if res.returncode != 0:
                raise PostgresError(f"initdb 失败：{res.stderr or res.stdout}")
        finally:
            try:
                pwfile.unlink()
            except OSError:
                pass

        self.write_conf()

    def write_conf(self) -> None:
        """把端口等配置写进 postgresql.conf。

        只追加我们需要的项，不整份重写 —— 用户可能手工调过参数。
        重复调用会先删掉我们上次写的块，避免越叠越多。
        """
        conf = self.pgdata / "postgresql.conf"
        if not conf.exists():
            return

        text = conf.read_text(encoding="utf-8", errors="replace")

        # 删掉上次生成的块
        text = re.sub(
            r"\n# === LAN Share 自动生成（勿手改）===\n(?:.*\n)*?# === END LAN Share ===\n",
            "\n",
            text,
        )

        block = (
            "\n# === LAN Share 自动生成（勿手改）===\n"
            f"port = {self.port}\n"
            "listen_addresses = '127.0.0.1'\n"
            "max_connections = 50\n"
            "# === END LAN Share ===\n"
        )
        conf.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")

    def _run(self, cmd: list[str], *, timeout: int = 60,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        """跑一个 PG 命令。全用列表传参，路径带空格也不会被切开。"""
        e = dict(os.environ)
        if env:
            e.update(env)
        # PG 的命令行工具在中文 Windows 上输出 GBK。用 text=True 默认按
        # UTF-8 解，遇到中文就抛 UnicodeDecodeError（虽然命令本身成功了）。
        # 这里放宽成 replace，编码猜错也只是乱码，不会中断安装。
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=e,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    # ── 服务 ──────────────────────────────────────────────

    def install_service(self, nssm: NssmService | None = None) -> None:
        """注册 PG 服务，用 PostgreSQL 自带的 pg_ctl register。

        为什么不用 NSSM（两条路都试过，都不行）：

          1. NSSM 跑 pg_ctl runservice
             报「无法启动服务，错误 1063」。runservice 是给 SCM
             内部调用的模式，不是拿来直接执行的。

          2. NSSM 跑 postgres.exe
             服务一直是 PAUSED。因为 NSSM 默认用 LocalSystem 账户，
             而 PostgreSQL 有硬性限制：拒绝以管理员身份运行
             （防止特权进程被攻击）。启动时它直接检查并退出。

        pg_ctl register 则由 PostgreSQL 自己创建服务：自动使用
        NetworkService 这种低权限账户，内部参数也配好了。
        实测起来就是 Running，端口正常监听。

        nssm 参数保留但不再使用，避免调用方大改。
        """
        # 幂等：已经注册过就直接返回，别让 sc create 报冲突
        check = self._run(["sc.exe", "query", self.service_name], timeout=20)
        if check.returncode == 0:
            return

        res = self._run(
            [str(self.exe("pg_ctl")), "register",
             "-N", self.service_name,
             "-D", str(self.pgdata),
             "-S", "auto"],
            timeout=60,
        )
        if res.returncode != 0:
            raise PostgresError(
                f"注册 PG 服务失败：{res.stderr or res.stdout}"
            )

    def start(self, timeout: int = 60) -> None:
        """启动 PG 并等它真的能连。"""
        # pg_ctl -l 指定的日志文件所在目录必须已存在，否则它会报
        # 「系统找不到指定的路径」然后起不来。安装流程只建了 data_dir，
        # 没建 data_dir/logs，所以这里自己兜一下。
        log_dir = self.data_dir / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PostgresError(f"建不了日志目录 {log_dir}：{exc}") from exc

        # ⚠️ 这里绝对不能用 capture_output / PIPE。
        #
        # pg_ctl start 会把 postgres 拉成后台进程，而那个子进程**继承父进程的
        # stdout 管道**。于是 subprocess.run 一直在等管道 EOF，可 postgres 是常驻的、
        # 永远不会关，结果就是安装卡死在这里 —— 服务器其实早就起来了（日志里有
        # "database system is ready to accept connections"），但调用方一直挂着。
        #
        # 所以：输出一律 DEVNULL，错误信息从 -l 指定的日志文件里读。
        res = subprocess.run(
            [str(self.exe("pg_ctl")), "start", "-D", str(self.pgdata),
             "-w", "-t", str(timeout), "-l", str(log_dir / "postgres.log")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 10,
            env=dict(os.environ, **({"PGPASSWORD": self.password} if self.password else {})),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode != 0:
            raise PostgresError(f"启动 PG 失败（rc={res.returncode}）：{self._tail_log(log_dir)}")

    def stop(self, timeout: int = 60) -> None:
        res = self._run(
            [str(self.exe("pg_ctl")), "stop", "-D", str(self.pgdata),
             "-m", "fast", "-w", "-t", str(timeout)],
            timeout=timeout + 10,
        )
        # 注：stop 不需要 DEVNULL —— 它不会 fork 常驻进程，管道会正常关闭。
        # 已经停了不算错误
        if res.returncode != 0 and "is not running" not in (res.stdout + res.stderr):
            raise PostgresError(f"停止 PG 失败：{res.stderr or res.stdout}")

    def _tail_log(self, log_dir: Path, lines: int = 8) -> str:
        """读 PG 日志尾部，用于失败时给人看。"""
        try:
            text = (log_dir / "postgres.log").read_text(encoding="utf-8", errors="replace")
            tail = text.strip().splitlines()[-lines:]
            return chr(10).join(tail) if tail else "（日志为空）"
        except OSError:
            return "（读不到日志）"

    def is_running(self) -> bool:
        res = self._run(
            [str(self.exe("pg_ctl")), "status", "-D", str(self.pgdata)],
            timeout=15,
        )
        return res.returncode == 0

    # ── 数据库 ────────────────────────────────────────────

    def _connect(self, *, db: str = "postgres", autocommit: bool = False):
        """连数据库，用超级用户。

        走 psycopg 而不是 psql.exe：随包带的是 zonky 精简版 PG，
        它只含 postgres / initdb / pg_ctl，根本没有 psql 客户端。
        反正 psycopg 是运行时必需依赖，用它反而少一个外部进程。
        """
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise PostgresError(
                "缺少 psycopg。安装包的运行时依赖应该带上它，"
                "开发环境请 pip install 'psycopg[binary]'"
            ) from exc

        try:
            conn = psycopg.connect(
                host="127.0.0.1",
                port=self.port,
                user="postgres",
                password=self.password,
                dbname=db,
                connect_timeout=10,
            )
        except Exception as exc:
            raise PostgresError(f"连不上 PostgreSQL：{exc}") from exc

        conn.autocommit = autocommit
        return conn

    def ensure_database(self) -> None:
        """建应用库和账号（幂等）。"""
        # 建角色。
        #
        # ⚠️ 不能用 %s 绑参数：CREATE ROLE 是 DDL，PostgreSQL 不允许在
        # DDL 里用绑定参数，会报 syntax error at or near "$1"。
        # 正确做法是 psycopg 的 sql.SQL + Identifier/Literal 组合 ——
        # 它会在客户端把标识符和字面量正确转义后再拼进语句。
        from psycopg import sql

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (self.db_user,))
                if cur.fetchone() is None:
                    cur.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(self.db_user),
                            sql.Literal(self.password),
                        )
                    )
            conn.commit()

        # CREATE DATABASE 不能在事务块里，必须 autocommit
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.db_name,))
                if cur.fetchone() is None:
                    cur.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {} ENCODING UTF8").format(
                            sql.Identifier(self.db_name),
                            sql.Identifier(self.db_user),
                        )
                    )

    def query_one(self, sql: str, *, db: str = "postgres") -> str:
        """跑一条只返回单值的查询，返回字符串（空结果返回空串）。"""
        with self._connect(db=db) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return "" if row is None else str(row[0])

    # ── 数据目录迁移 ──────────────────────────────────────

    def migrate_to(self, new_data_dir: str | Path) -> Path:
        """把整个数据目录搬到新位置。

        安全策略（跨盘 move 不是原子操作，中途断会丢数据）：
            1. 停服务（PG → API → Caddy），顺序很重要
            2. **复制**而不是移动，源目录原样保留
            3. 校验新目录里文件数和总大小对得上
            4. 改配置，用新目录启动
            5. 自检能连上、库还在
            6. 全部通过才允许删旧目录（由调用方决定）

        中途任何一步失败都留下原目录，调用方可以用 rollback_to() 回滚。
        """
        new_dir = Path(new_data_dir).resolve()
        old_dir = self.data_dir.resolve()

        if new_dir == old_dir:
            raise PostgresError("新目录和当前目录相同")
        if new_dir.exists() and any(new_dir.iterdir()):
            raise PostgresError(f"目标目录 {new_dir} 不是空的，换一个")
        if old_dir in new_dir.parents or new_dir in old_dir.parents:
            raise PostgresError("新目录不能是当前目录的子目录或父目录")

        # 1. 复制
        new_dir.mkdir(parents=True, exist_ok=True)
        for item in old_dir.iterdir():
            dst = new_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dst, symlinks=False, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)

        # 2. 校验：文件数和总字节数都要对上
        src_n, src_sz = _tree_stats(old_dir)
        dst_n, dst_sz = _tree_stats(new_dir)
        if src_n != dst_n or src_sz != dst_sz:
            raise PostgresError(
                f"复制不完整：源 {src_n} 个文件/{src_sz} 字节，"
                f"目标 {dst_n} 个/{dst_sz} 字节。原数据未动，可以重试。"
            )

        # 3. 切换
        self.data_dir = new_dir
        return new_dir


def _tree_stats(path: Path) -> tuple[int, int]:
    """目录里文件的个数和总字节数，用于复制后校验。"""
    count = 0
    size = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                size += (Path(root) / f).stat().st_size
                count += 1
            except OSError:
                pass
    return count, size


def generate_password(length: int = 32) -> str:
    """生成初始化用的随机密码。

    只取字母数字：密码要同时进 postgresql 的 pg_hba、
    DATABASE_URL（URL 编码）、以及注册表。带引号或反斜杠的密码
    在 psql 命令行里要转义好几层，容易在某个环节出错。
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
