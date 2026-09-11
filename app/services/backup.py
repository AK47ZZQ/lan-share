"""每日元数据库备份编排（B5）：DB dump + 元数据快照 → BACKUP_DIR。

## 编排

- 复用 cleanup 注册式定时调度（register_sweep，清理循环每小时醒一轮）：
  `_tick` 检查"今天是否已备份"（settings 存日期），到 BACKUP_HOUR 后的
  首个本轮补跑——服务凌晨重启也不会漏当天备份；
- dump：SQLite 走 `sqlite3.Connection.backup`（在线一致性快照，不影响
  服务中的连接）；PG 走 `pg_dump` 子进程（DATABASE_URL 直传）——
  pg_dump 不在 PATH 时记失败并走 webhook 告警（不静默假装成功）；
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
import sqlite3
import time
from pathlib import Path

from app.config import BACKUP_DIR, BACKUP_HOUR, BACKUP_KEEP, DB_PATH
from app.db import DATABASE_URL, USE_PG, _db
from app.settings import get_setting, set_setting
from app.services import cleanup
from app.services import webhook as _webhook

STATUS_KEY = "backup_last_status"
LAST_DATE_KEY = "backup_last_date"

_NO_DUMP = object()   # dump 阶段"引擎不支持/工具缺失"哨兵


def ensure_scheduled() -> None:
    """注册进 cleanup sweep（幂等：模块级一次；由 app/main 组装时调用）。"""
    cleanup.register_sweep("daily_backup", _tick)


def _tick() -> None:
    """每小时被 sweep 唤醒：到点且今天未备 → 备份。"""
    today = time.strftime("%Y-%m-%d")
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
        meta = BACKUP_DIR / f"settings-{stamp}.json"
        _write_settings_snapshot(meta)
        status["files"].append(str(meta))
        _prune_old()
        status["ok"] = True
        status["detail"] = f"{len(status['files'])} 份"
        set_setting(LAST_DATE_KEY, time.strftime("%Y-%m-%d"))
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
    『看似完整』的假备份）。返回落点 Path；引擎/工具不可用返回 _NO_DUMP。"""
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    result = _dump_engine(tmp)
    if result is _NO_DUMP:
        tmp.unlink(missing_ok=True)
        return _NO_DUMP
    tmp.replace(dst)
    return dst


def _dump_engine(tmp: Path):
    if USE_PG:
        from shutil import which
        if which("pg_dump") is None:
            return _NO_DUMP
        # list 形式传参免 shell 解释（终审 P0：os.system + f-string 拼连接串
        # 存在命令注入面，DATABASE_URL 虽由管理员环境变量控制也不该依赖它）
        import subprocess
        r = subprocess.run(["pg_dump", DATABASE_URL, "-f", str(tmp)],
                           capture_output=True, text=True, timeout=600)
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
