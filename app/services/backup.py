"""每日元数据库备份编排（B5）：DB dump + 元数据快照 → BACKUP_DIR。

## 编排

- 复用 cleanup 注册式定时调度（register_sweep，清理循环每小时醒一轮）：
  `_tick` 检查"今天是否已备份"（settings 存日期），到 BACKUP_HOUR 后的
  首个本轮补跑——服务凌晨重启也不会漏当天备份；
- dump：SQLite 走 `sqlite3.Connection.backup`（在线一致性快照，不影响
  服务中的连接）；PG 走 `pg_dump` 子进程（DATABASE_URL 直传，`-Fc` custom
  格式，与外部主备份 backup.bat 的产物格式一致）——
  pg_dump 不在 PATH 时记失败并走 webhook 告警（不静默假装成功）；
- **验证（restore drill 内联）**：dump 落盘后立即做引擎恰当的完整性校验
  （SQLite `PRAGMA integrity_check` + settings 表可读；PG `pg_restore -l`
  读归档目录并核对 settings 表在档）——"生成成功"不等于"产物有效"，验证
  失败按备份失败处理（告警上门、不写当日幂等位，次日自然重试）**并删除
  坏产物**（不留占盘的"看似备份"）；校验工具缺失呈现
  `verified=None`（诚实呈现，不假装通过也不误报失败）；
- dump 子进程连接参数由 DATABASE_URL 拆解，**口令只进子进程环境
  （PGPASSWORD），不出现在命令行**（与 backup.bat 的 PGPASSFILE 纪律对齐）；
- 元数据快照：settings 表全量 + 版本信息导 JSON（丢库后重建配置的最小集）；
- 保留策略：BACKUP_KEEP 份滚动删除；状态写 settings（healthreport 与
  /api/metrics 只读呈现），失败走 services/webhook（SSRF 防护通道）。

## 边界

- 备份目录在数据目录内（.backups）：文件名带时间戳、写入临时名后原子改名，
  半途失败不留下"看似完整"的假备份。
- 本模块不认识列名（settings 走 app/settings.py 语义出口），DB 结构知识
  不出 store 层。
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from app.config import BACKUP_DIR, BACKUP_HOUR, BACKUP_KEEP, DB_PATH, PG_BIN_FALLBACK
from app.db import DATABASE_URL, USE_PG, _db
from app.settings import get_setting, set_setting
from app.services import cleanup
from app.services import webhook as _webhook

STATUS_KEY = "backup_last_status"
LAST_DATE_KEY = "backup_last_date"

_NO_DUMP = object()   # dump 阶段"引擎不支持/工具缺失"哨兵


def _resolve_tool(name: str) -> str | None:
    """PG 客户端工具解析：PATH → 部署回退目录（config.PG_BIN_FALLBACK）。

    服务进程 PATH 不可依赖——NSSM 环境随重启刷新（2026-09-12 实录：旧实例
    靠残留环境跑成 dump，重启后系统 PATH 已无 pgsql bin，当晚备份险些断链）。
    返回可执行文件路径；两处都找不到返回 None（调用方按"工具缺失"语义处理）。
    """
    from shutil import which
    p = which(name)
    if p:
        return p
    cand = Path(PG_BIN_FALLBACK) / f"{name}.exe"
    return str(cand) if cand.is_file() else None


def _today() -> str:
    """当日幂等位的日期出口（测试注入点）。

    _tick 与 run_backup_once 若直呼 time.strftime，"写幂等位"与测试的
    "断言读取"可能分踞午夜两侧（真实日期在两者之间翻转）——幂等位与
    断言基准错位，偶发 1 failed 且连跑无法复现。编排统一经本函数取日期，
    测试钉死后时钟无关。
    """
    return time.strftime("%Y-%m-%d")


def ensure_scheduled() -> None:
    """注册进 cleanup sweep（幂等：模块级一次；由 app/main 组装时调用）。"""
    cleanup.register_sweep("daily_backup", _tick)


def _tick() -> None:
    """每小时被 sweep 唤醒：到点且今天未备 → 备份。"""
    today = _today()
    if get_setting(LAST_DATE_KEY) == today:
        return
    if time.localtime().tm_hour < BACKUP_HOUR:
        return
    run_backup_once()


def run_backup_once(push=None) -> dict:
    """立即备份一轮（调度与 admin 手动触发共用；返回状态 dict）。"""
    if push is None:
        push = _webhook.push_webhook
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    status: dict = {"ts": time.time(), "ok": False, "detail": "", "files": []}
    try:
        dump = _dump_to(BACKUP_DIR / f"meta-{stamp}.db")
        if dump is _NO_DUMP:
            status["detail"] = "当前引擎无可用 dump 工具（PG 需 pg_dump 在 PATH）"
            raise RuntimeError(status["detail"])
        status["files"].append(str(dump))
        verified, verify_detail = _verify_dump(dump)
        if verified is False:
            dump.unlink(missing_ok=True)   # 坏产物不留"备份"之名：占 keep 组、占盘，且可能被人当真
            status["detail"] = f"备份验证失败: {verify_detail}"
            raise RuntimeError(status["detail"])
        status["verified"] = verified
        status["verify_detail"] = verify_detail
        meta = BACKUP_DIR / f"settings-{stamp}.json"
        _write_settings_snapshot(meta)
        status["files"].append(str(meta))
        _prune_old()
        status["ok"] = True
        status["detail"] = f"{len(status['files'])} 份"
        set_setting(LAST_DATE_KEY, _today())
    except Exception as e:   # 告警路径：任何失败都要"上门提醒"，不只记档
        status["detail"] = str(e)[:200]
        push("备份失败", status["detail"])
    set_setting(STATUS_KEY, json.dumps(status, ensure_ascii=False))
    return status


def last_status() -> dict | None:
    raw = get_setting(STATUS_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _dump_to(dst: Path):
    """引擎中立 dump。落 .tmp 临时名、成功后原子改名（终审 P1：半途崩溃不留
    『看似完整』的假备份）。返回落点 Path；引擎/工具不可用返回 _NO_DUMP。
    引擎中途抛错（pg_dump 超时/崩溃）同样清掉 .tmp——残留物要等下一轮成功
    才会被 prune 收走，磁盘吃紧时等于慢性泄漏。"""
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    try:
        result = _dump_engine(tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if result is _NO_DUMP:
        tmp.unlink(missing_ok=True)
        return _NO_DUMP
    tmp.replace(dst)
    return dst


def _pg_dump_cmd(url: str) -> tuple[list[str], dict[str, str]]:
    """DATABASE_URL → pg_dump 连接参数 argv + 口令环境变量。

    口令**绝不进命令行**——Windows 上任何进程都能读到别家进程的命令行
    （任务管理器/WMI），与外部主备份 backup.bat 的 PGPASSFILE 纪律对齐：
    argv 只放非敏感连接参数，口令走 PGPASSWORD 环境变量（子进程独享副本）。
    URL 缺某字段就不传对应参数（libpq 按标准默认值解析），解析不出即
    响亮失败，不做静默回退。
    """
    parts = urlsplit(url)
    argv: list[str] = []
    if parts.hostname:
        argv += ["-h", parts.hostname]
    if parts.username:
        argv += ["-U", unquote(parts.username)]
    if parts.port:
        argv += ["-p", str(parts.port)]
    dbname = unquote(parts.path.lstrip("/"))
    if dbname:
        argv += ["-d", dbname]
    env: dict[str, str] = {}
    if parts.password is not None:
        env["PGPASSWORD"] = unquote(parts.password)
    return argv, env


def _dump_engine(tmp: Path):
    if USE_PG:
        dump_tool = _resolve_tool("pg_dump")
        if dump_tool is None:
            return _NO_DUMP
        # list 形式传参免 shell 解释（终审 P0）；连接参数由 _pg_dump_cmd 拆解，
        # 口令只进子进程环境不进 argv；-Fc custom 格式：pg_restore -l 可校验，
        # 且与外部主备份格式一致
        import subprocess
        argv, pw_env = _pg_dump_cmd(DATABASE_URL)
        r = subprocess.run([dump_tool, *argv, "-Fc", "-f", str(tmp)],
                           capture_output=True, text=True, timeout=600,
                           env={**os.environ, **pw_env})
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump 失败: {r.stderr[-200:]}")
        return tmp if tmp.exists() else _NO_DUMP
    src = sqlite3.connect(str(DB_PATH))
    try:
        out = sqlite3.connect(str(tmp))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return tmp


def _pg_toc_has_public_settings(toc: str) -> bool:
    """TOC 里必须有 **public** schema 的 settings 表数据。

    pg_restore -l 行形如 `301; 1259 19302 TABLE DATA <schema> <table> <owner>`。
    2026-09-13 实锤：生产库残留 ls_test_* 测试 schema（PG 双引擎测试指向生产
    库、异常中断未清理）时，宽松的 `\\bsettings\\b` 会匹配测试 schema 的
    settings——public.settings 缺失也能"验证通过"（假阳性备份）。"""
    for line in toc.splitlines():
        if not re.search(r"\bTABLE DATA\b", line):
            continue
        parts = line.split()
        # < knobs ; ... > TABLE DATA schema table owner
        if len(parts) >= 6 and parts[-3] == "public" and parts[-2] == "settings":
            return True
    return False


def _verify_dump(dst: Path) -> tuple[bool | None, str]:
    """dump 完整性校验（restore drill 内联）。返回 (结果, 说明)：

    - True：校验通过；False：产物损坏（编排按备份失败处理——告警 + 幂等位不写）；
    - None：校验工具缺失（诚实呈现，不假装通过也不误报）。
    """
    try:
        if USE_PG:
            restore_tool = _resolve_tool("pg_restore")
            if restore_tool is None:
                return None, "pg_restore 不可达（PATH 与回退目录均无），无法校验归档"
            import subprocess
            r = subprocess.run([restore_tool, "-l", str(dst)],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return False, f"pg_restore -l 失败: {r.stderr[-150:]}"
            # TOC 可读之外还要 settings 表在档（与 SQLite 侧"settings 可读"对称）：
            # 空档期/半截归档可能目录能读却缺核心表
            if not _pg_toc_has_public_settings(r.stdout or ""):
                return False, "归档目录缺 public.settings 表（TOC 无 TABLE DATA public settings）"
            return True, "归档目录可读（public.settings 表在档）"
        # URI 百分号编码：SHARE_ROOT 含 %/#/? 等字符时裸拼会被 URI 语法吃掉，
        # 校验误报失败（误告警）；Windows 盘符冒号保留（safe 里的 ':'）
        con = sqlite3.connect(f"file:{quote(dst.as_posix(), safe='/:')}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            # settings 表可读：settings 快照恢复依赖的核心表确实在 dump 里
            # （backup 模块已持有该口径，见 _write_settings_snapshot）
            n = con.execute("SELECT count(*) FROM settings").fetchone()[0]
        finally:
            con.close()
        if row[0] != "ok":
            return False, f"integrity_check: {row[0]}"
        return True, f"integrity ok（settings {n} 条）"
    except Exception as e:
        return False, str(e)[:200]


def _write_settings_snapshot(p: Path) -> None:
    """settings 表全量（配置即元数据的最小重建集）+ 版本信息。"""
    rows = _db().execute("SELECT key, value FROM settings").fetchall()
    payload = {
        "kind": "lan-share-settings-snapshot",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": {r["key"]: r["value"] for r in rows},
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)   # 原子落位：不留半写假备份


def _prune_old() -> None:
    """滚动保留最近 BACKUP_KEEP **组**（终审 P1 修复：原按文件名排序时
    meta-* 字典序恒前于 settings-*，切片会先删光全部 dump——改为按
    时间戳分组，组为单位整组淘汰，dump 与快照保持成对）。"""
    import re
    groups: dict[str, list] = {}
    for f in BACKUP_DIR.iterdir():
        m = re.match(r"(meta|settings)-(\d{8}-\d{6})\.(db|json)$", f.name)
        if m:
            groups.setdefault(m.group(2), []).append(f)
        else:
            try:
                f.unlink()   # 历史半写残留/临时文件：直接清
            except OSError:
                pass
    keep = sorted(groups)[-BACKUP_KEEP:]
    for stamp, files in groups.items():
        if stamp not in keep:
            for f in files:
                try:
                    f.unlink()
                except OSError:
                    pass
