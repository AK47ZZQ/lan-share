"""F4 流量仪表：traffic.summarize 聚合口径的回归。

用临时日志目录喂固定行（正常/轮转残卷/坏行/跨月/非访问日志），钉住三件事：
① 只有合法 JSON 且带数值 ts+size 的行才计数；② 本地日界聚合与 days 截断；
③ month 聚合含 days 窗口之外的当月旧日（月初的用量也必须算进本月）。
"""
import json
from datetime import datetime
from pathlib import Path

from app.services import traffic

# 固定"现在"= 2026-09-29 12:00 本地时间：当月 9/1 距今 28 天，落在 14 天
# 窗口之外——正好分离"days 窗口"与"本月聚合"两个口径（9 月 11 日测不出）
NOW = datetime(2026, 9, 29, 12, 0, 0).timestamp()


def _log_dir(tmp_path: Path, lines_by_file: dict[str, list]) -> Path:
    d = tmp_path / "access"
    d.mkdir()
    for name, lines in lines_by_file.items():
        (d / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _line(ts: float, size: int) -> str:
    return json.dumps({"ts": ts, "size": size, "status": 200,
                       "request": {"remote_ip": "127.0.0.1", "method": "GET",
                                   "uri": "/files/x.jpg"}})


def test_today_month_and_days_windows(tmp_path):
    today = datetime(2026, 9, 29, 10, 0, 0).timestamp()
    yesterday = datetime(2026, 9, 28, 23, 0, 0).timestamp()
    month_old_outside_window = datetime(2026, 9, 1, 8, 0, 0).timestamp()
    last_month = datetime(2026, 8, 20, 8, 0, 0).timestamp()
    d = _log_dir(tmp_path, {
        "access.log": [_line(today, 100), _line(yesterday, 200),
                       _line(month_old_outside_window, 400),
                       _line(last_month, 9999)],
        # 轮转残卷：Caddy 窗口化轮转会带时间戳后缀，解析必须不挑文件名
        "access.log-2026-09-29T00-00-00.000.log": [_line(today, 50)],
    })
    r = traffic.summarize(days=14, now=NOW, log_dir=d)
    assert r["today_bytes"] == 150          # 100 + 轮转卷里的 50
    assert r["month_bytes"] == 750          # 150 + 200 + 400；上月 9999 不计
    assert r["free_tier_bytes"] == 5 * 1024 ** 3
    days = {x["date"]: x["bytes"] for x in r["days"]}
    assert len(r["days"]) == 14             # 9/16 ~ 9/29
    assert days["2026-09-29"] == 150
    assert days["2026-09-28"] == 200
    assert "2026-09-01" not in days         # 窗口外当月旧日不进图表…
    assert r["month_bytes"] >= 400          # …但必须进月用量（上面 750 已含）


def test_bad_lines_and_missing_dir(tmp_path):
    d = _log_dir(tmp_path, {"access.log": [
        "not json at all",
        json.dumps({"ts": "oops", "size": 1}),          # 非数值 ts
        json.dumps({"ts": datetime(2026, 9, 29, 9, 0, 0).timestamp(),
                    "size": None}),                      # size 缺失
        _line(datetime(2026, 9, 29, 9, 0, 0).timestamp(), 7),
    ]})
    r = traffic.summarize(days=3, now=NOW, log_dir=d)
    assert r["today_bytes"] == 7            # 只有合法行计数，坏行不炸

    # 目录不存在（Caddy 未开日志的新装形态）→ 全零而不是异常；
    # days 恒为固定长度序列（前端画图不需要判空），只是全为 0
    r2 = traffic.summarize(days=3, now=NOW, log_dir=tmp_path / "nope")
    assert r2["today_bytes"] == 0 and r2["month_bytes"] == 0
    assert len(r2["days"]) == 3 and all(x["bytes"] == 0 for x in r2["days"])
