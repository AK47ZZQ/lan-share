"""公网流量仪表（F4）：解析 Caddy 隧道入口访问日志，量化樱花FRP 月流量。

为什么只记隧道块：Caddyfile 里 access log 只挂在 :8767/:443/:8801（隧道/备用
入口），LAN 直连 :8765 产生不了日志——"有日志 ≡ 走隧道 ≡ 消耗 FRP 配额"，
判别零歧义；也不需要按 remote_ip 区分（frp 本地转发让所有公网请求都长着
127.0.0.1 的脸，IP 不可判）。

口径与局限：size = Caddy 写给客户端的字节数（下行为主：照片浏览/分享下载/
DAV 读盘），请求体（公网上传方向）不入日志、不计入——真值以樱花FRP 面板为
准，本仪表定位是"日常趋势与用量预警"，不做计费。管理台读数频率远低于日志
行增长速度，全量解析在家庭规模（日均千行级）无压力。

轮转与保留：Caddy 侧 roll_size 10MiB × roll_keep 5 ≈ 至多 50MiB 磁盘占用；
本模块只读（glob access.log*，轮转命名无关紧要），不清理、不持锁。
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.config import SHARE_ROOT

LOG_DIR = SHARE_ROOT / "logs" / "access"   # SHARE_ROOT 即仓库 data/，别再拼一层
FREE_TIER_BYTES = 5 * 1024 ** 3   # 樱花FRP 免费档月流量（对照预警用）
DEFAULT_DAYS = 14


def iter_log_lines(log_dir: Path | None = None):
    """遍历全部访问日志行（含轮转残卷），单文件不可读/坏行静默跳过。

    静默的理由：日志是旁路观测数据，解析失败不该让管理台 500；
    但 0 字节目录（Caddy 未开日志/新装）也返回空——调用方据 None/空区分。
    """
    d = log_dir if log_dir is not None else LOG_DIR
    if not d.is_dir():
        return
    for f in sorted(d.glob("access.log*")):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            yield line


def summarize(days: int = DEFAULT_DAYS, now: float | None = None,
              log_dir: Path | None = None) -> dict:
    """按本地日聚合隧道出口流量，供 /api/admin/overview 的 traffic 块。

    日界用本地时区（"家里今天用了多少"的直觉）；days 图表截现在往前推。
    """
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    day_start = {today - timedelta(days=i): 0.0 for i in range(days)}
    month_prefix = f"{today.year:04d}-{today.month:02d}"

    month_bytes = 0.0
    for line in iter_log_lines(log_dir):
        try:
            entry = json.loads(line)
            size = entry.get("size") or 0
            ts = entry.get("ts")
            if not isinstance(size, (int, float)) or not isinstance(ts, (int, float)):
                continue
        except (ValueError, TypeError):
            continue
        d = datetime.fromtimestamp(ts).date()
        if d in day_start:
            day_start[d] += size
        if d.strftime("%Y-%m") == month_prefix:
            month_bytes += size

    return {
        "today_bytes": int(day_start[today]),
        "month_bytes": int(month_bytes),
        "free_tier_bytes": FREE_TIER_BYTES,
        "days": [
            {"date": d.isoformat(), "bytes": int(b)}
            for d, b in sorted(day_start.items(), reverse=True)
        ],
    }
