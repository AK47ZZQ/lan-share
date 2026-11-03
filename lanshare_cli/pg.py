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
import sys
import re
import secrets
import shutil
import string
import time
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
        """PG 数据目录 = 数据目录的**同级** pgdata。

        🔴 不能放在 data_dir 里面（2026-11-03 实机）。data_dir 是**用户文件根**，
        网盘会把里面的东西原样列给用户看。pgdata 混进去之后，用户在手机端
        发现自己网盘里凭空多了一个叫 pgdata 的目录，里面全是 PostgreSQL 的
        二进制文件，既看不懂也不敢删。程序自己的状态（数据库、CA 证书、日志）
        都不该出现在用户文件列表里。
        """
        return self.data_dir.parent / "pgdata"

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

    def _secure_data_dir(self) -> None:
        """把数据目录的 owner 和 ACL 收成 PostgreSQL 能接受的样子。

        ## 为什么必须做

        `initdb` 不只看「能不能写」，它要求数据目录：

          · owner 是当前用户
          · 没有从父目录继承来的宽松 ACL（Users 只读也不行）

        `C:\\Program Files` 两条都不满足 —— owner 是 SYSTEM，ACL 继承自
        系统目录。所以在 Program Files 下装（安装器的默认路径）时，
        initdb 直接报 `Permission denied`，整个安装停在 [7/10]：
        文件都就位了，服务一个都没起来。

        本机开发目录（`C:\\lan-share`）恰好是当前用户建的、ACL 干净，
        所以这个 bug 只在干净环境暴露 —— 沙箱里一测就现原形。

        ## 为什么不用「改默认数据目录」

        默认数据目录跟着安装目录走是刻意的（用户只选一次路径，程序和
        数据在一起，备份时整个复制走）。为了迁就 initdb 把数据赶到
        ProgramData，等于让用户记两个路径 —— 那个体验问题比这个权限
        问题更难解释。

        ## 失败不阻断

        非管理员 / 非 Windows 环境下 icacls 可能不可用。真失败了让
        initdb 自己去报它自己的错，比在这里编一个错误清楚。
        """
        # 用 sys.platform 而不是 os.name：测试里 monkeypatch os.name 会把
        # pathlib 一起改坏（Path 变成 PosixPath，Windows 上直接崩）。
        if sys.platform != "win32":
            return
        user = os.environ.get("USERNAME", "")
        if not user:
            return
        try:
            # /inheritance:r 断开继承；/grant:r 只留当前用户完全控制。
            # 两条一起做，顺序不能反 —— 先 grant 再断继承会把刚加的也清掉。
            self._run(["icacls", str(self.pgdata),
                       "/inheritance:r",
                       "/grant:r", f"{user}:(OI)(CI)F"],
                      timeout=60)
            # owner 也改掉：initdb 会检查「目录是不是我自己的」
            self._run(["icacls", str(self.pgdata), "/setowner", user],
                      timeout=60)
        except Exception:
            # 权限收拾失败不该在这里报错 —— 让 initdb 用它自己的话去说
            pass

    def _grant_service_access(self) -> None:
        """把数据目录的访问权还给「服务账户」。

        ## 为什么需要第二步

        前面 `_secure_data_dir()` 把 pgdata 收成「只有当前用户」，那是
        `initdb` 的要求。但服务是以 LocalSystem 注册的（`pg_ctl register`
        的默认行为），它以另一个身份访问同一个目录——

          收紧 → initdb 能跑 → 服务起不来（STOPPED）
          不收 → initdb 直接拒绝（Permission denied）

        所以必须两步：initdb 之前收紧，initdb 之后再把服务账户加回来。

        实测顺序错掉的症状：`[9/10] 启动服务 FAIL lanshare-postgresql: STOPPED`，
        而 `[7/10] initdb` 和 `[8/10] 注册服务` 都是 OK —— 很容易误判成
        「PG 二进制有问题」。
        """
        if sys.platform != "win32":
            return
        # LocalSystem 的正式名字；SID 形式 S-1-5-18 同样有效，但名字更可读
        try:
            self._run(["icacls", str(self.pgdata),
                       "/grant", r"NT AUTHORITY\SYSTEM:(OI)(CI)F"],
                      timeout=60)
        except Exception:
            pass

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
        # 先把目录建出来，才能给它设权限（initdb 会拒绝「不存在的目录」吗？
        # 不会 —— 但它会自己建，而自建的目录会继承父目录 ACL，正好是要避免的）
        self.pgdata.mkdir(parents=True, exist_ok=True)
        self._secure_data_dir()

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
        # initdb 跑完了，把权限还给服务账户 —— 不然服务起不来（STOPPED）
        self._grant_service_access()

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
        # 幂等，但要校验 -D。
        #
        # 🔴 这里原来只有一句「服务已存在就 return」，埋了一个很难看的坑：
        # 用户重装时如果数据目录换了（比如从 {app}\data 改成 ProgramData，
        # 或者反过来），服务不会被重建，-D 还指着**旧目录**。表现是 PG 服务
        # 一直 Stopped，而注册表说数据目录是新那个 —— 两边对不上，谁也看不出
        # 是服务定义过期了。2026-11-02 用户实机正是这个：
        #   服务定义 -D = D:\LAN Share\data\pgdata
        #   注册表 DataFolder = C:\ProgramData\LANShare\data
        check = self._run(["sc.exe", "query", self.service_name], timeout=20)
        if check.returncode == 0:
            qc = self._run(["sc.exe", "qc", self.service_name], timeout=20)
            current = (qc.stdout or "") + (qc.stderr or "")
            if str(self.pgdata) in current:
                return                      # 指向的就是当前 pgdata，保持不动
            # 指向别处 → 删掉重建（数据本身不动，只换服务定义）
            self._run(["sc.exe", "delete", self.service_name], timeout=30)
            for _ in range(20):             # sc delete 是异步的，等它真消失
                if self._run(["sc.exe", "query", self.service_name],
                             timeout=10).returncode != 0:
                    break
                time.sleep(0.5)
            else:
                raise PostgresError(
                    f"旧服务 {self.service_name} 删不掉，-D 还指向 {current[:120]}")

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

    def migrate_legacy_pgdata(self) -> bool:
        """把 3.94 及以前塞在 ``<data_dir>\\pgdata`` 的库搬到 ``<data_dir>`` 的同级。

        返回 True 表示这次真的搬了。

        调用前必须已经停掉 PG 服务：服务定义里的 -D 还指着老路径，
        ``pg_ctl stop -D <新路径>`` 是找不到它的。

        同盘 move 走 rename，原子且瞬间完成；跨盘会退化成「复制 + 删除」，
        所以搬完必须校验 PG_VERSION 真的落地了，再让调用方重建服务定义。

        2026-11-04 加固（对抗式审查）：
          · 老位置没有 PG_VERSION → 在搬**之前**响亮失败。老布局下 pgdata
            在用户文件区里、对用户可见，用户可能往里放过文件 —— 先搬后验
            等于把用户文件夹悄悄挪出网盘列表。
          · move 抛异常 → 清掉本次调用留下的残缺副本（跨盘 move = 复制 +
            删除，中断必留半截），老位置原样保留，可以安全重试；否则重试
            会落进「两套库」响亮失败，用户被卡死在没做错任何事的路径上。
        """
        old = self.data_dir / "pgdata"
        new = self.pgdata

        if old.resolve() == new.resolve() or not old.is_dir():
            return False

        if not (old / "PG_VERSION").is_file():
            raise PostgresError(
                f"{old} 存在，但里面没有 PG_VERSION —— 它可能不是数据库目录"
                "（老布局下 pgdata 在用户文件区，用户可能往里放过文件）。\n"
                "为避免把用户文件误当数据库搬走，没有搬。请人工确认这个目录"
                "里是什么：是普通文件夹就改名或移走；确实是库（PG_VERSION 被误删）"
                "说明数据库已损坏，先按备份恢复再升级。"
            )

        if new.is_dir():
            raise PostgresError(
                "发现两套数据库目录，不知道该用哪一套：\n"
                f"  {old}\n  {new}\n\n"
                "请手工确认保留哪一套（另一套改名备份）之后再重装。"
            )

        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(old), str(new))
        except OSError as exc:
            # 清掉本次调用留下的残缺副本，让重试是安全的。
            # 只清「这次刚建、且没有 PG_VERSION」的 —— 万一里头已经有
            # PG_VERSION（复制其实完成了）就保留现场，别把搬成的删了。
            try:
                if new.is_dir() and not (new / "PG_VERSION").exists():
                    shutil.rmtree(new)
            except OSError:
                pass            # 清不掉就留着现场，错误信息里已说明老位置未动
            raise PostgresError(
                f"数据库目录搬迁失败：{exc}\n"
                f"老位置 {old} 未动，可以重试。") from exc

        if not (new / "PG_VERSION").is_file():
            raise PostgresError(
                f"数据库目录搬到了 {new}，但里面没有 PG_VERSION —— 搬迁可能没走完。\n"
                f"老位置 {old} 已经不在，请先检查 {new} 再启动服务。"
            )

        # 换了一级目录，SYSTEM / NetworkService 的 ACL 可能不跟着过来
        try:
            self._grant_service_access()
        except Exception:
            pass        # 权限失败不该拦住搬迁本身，启动时还会再试
        return True

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
