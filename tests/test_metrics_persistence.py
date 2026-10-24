"""v3.70 观测持久化钉：限速计数与启动史必须跨“重启”存活。

背景：本机服务每天随开关机重启，进程内计数天天清零——v3.69 造的限速观测
仪器（rate_rejects）结构上看不到任何完整的一天。本文件钉三件事：
  ① 计数 persist → 清内存 → load 后恢复；
  ② 新进程的增量在历史之上累计（不覆盖、不从零重报）；
  ③ 启动史环形封顶。
消融锚：把 persist_rejects/load_persisted_rejects 改成 no-op → ①红；
把 note_boot 的 append 删掉 → ③红。
"""
import json

from app.services import metrics as _m
from app.settings import get_setting, set_setting


def _reset_memory():
    _m._rate_rejects.clear()
    _m._rejects_baseline.clear()


def test_rate_rejects_survive_process_restart(client):
    _reset_memory()
    set_setting(_m.REJECTS_PERSIST_KEY, "")
    try:
        _m.record_rate_reject("media:lan")
        _m.record_rate_reject("media:lan")
        assert _m.snapshot()["rate_rejects"]["media:lan"] == 2
        _m.persist_rejects()
        # 模拟进程重启：内存全清，只从 settings 恢复基线
        _reset_memory()
        _m.load_persisted_rejects()
        assert _m.snapshot()["rate_rejects"]["media:lan"] == 2, (
            "重启（清内存+load）后计数应存活——观测仪器不许每天清零")
        # 新进程增量叠加在历史之上，不覆盖
        _m.record_rate_reject("media:lan")
        assert _m.snapshot()["rate_rejects"]["media:lan"] == 3
    finally:
        set_setting(_m.REJECTS_PERSIST_KEY, "")
        _reset_memory()


def test_load_tolerates_corrupt_payload(client):
    """坏基线数据按空处理，不炸主流程（load 在 lifespan 里，炸=服务起不来）。"""
    set_setting(_m.REJECTS_PERSIST_KEY, "not-json{{{")
    try:
        _m.load_persisted_rejects()
        assert _m._rejects_baseline == {}
    finally:
        set_setting(_m.REJECTS_PERSIST_KEY, "")
        _reset_memory()


def test_boot_history_grows_and_caps(client):
    set_setting(_m.BOOT_HISTORY_KEY, "")
    try:
        _m.note_boot()
        _m.note_boot()
        boots = json.loads(get_setting(_m.BOOT_HISTORY_KEY, "[]"))
        assert len(boots) == 2
        # 环形封顶：灌满后再记不超 CAP，且最新一条在尾部
        set_setting(_m.BOOT_HISTORY_KEY,
                    json.dumps([{"ts": 1}] * _m._BOOT_HISTORY_CAP))
        _m.note_boot()
        boots = json.loads(get_setting(_m.BOOT_HISTORY_KEY, "[]"))
        assert len(boots) == _m._BOOT_HISTORY_CAP
        assert boots[-1]["ts"] != 1
    finally:
        set_setting(_m.BOOT_HISTORY_KEY, "")
