"""后台循环任务持引用防 GC + register_sweep 去重回归钉（v3.67.5）。

R2-P3-8 同族：create_task 未保存引用的任务可能被 GC 回收——cleanup/
thumb_warmup/notify 三个 while-True 循环此前全部裸奔（healthreport 已修），
循环静默停摆 = 所有 sweep/预热/定时广播失效。register_sweep 无去重时，
模块二次加载会让同一 sweep 每轮跑两遍。
消融锚：把任一模块的 `_xxx_task = asyncio.create_task(...)` 退回裸调用 →
结构钉红；register_sweep 撤销去重 → 去重钉红。
"""
import asyncio

from app.services import cleanup as cleanup_svc


def test_register_sweep_dedup():
    def fn_a():
        pass

    def fn_b():
        pass

    before = list(cleanup_svc._sweeps)
    try:
        cleanup_svc.register_sweep("zzdedup", fn_a)
        cleanup_svc.register_sweep("zzdedup", fn_b)
        names = [n for n, _ in cleanup_svc._sweeps]
        assert names.count("zzdedup") == 1, "同名 sweep 被登记两次"
        # 后注册者替换前者（热重载语义）
        entry = [fn for n, fn in cleanup_svc._sweeps if n == "zzdedup"][0]
        assert entry is fn_b
    finally:
        cleanup_svc._sweeps[:] = [e for e in cleanup_svc._sweeps if e in before]


def test_cleanup_startup_holds_task_reference():
    async def main():
        await cleanup_svc.startup()
        assert cleanup_svc._cleanup_task is not None, "cleanup 循环任务无引用（可被 GC）"
        cleanup_svc._cleanup_task.cancel()

    asyncio.run(main())


def test_async_loop_sites_hold_task_references():
    """结构守卫：三处 while-True 循环的启动必须保存任务引用。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    for rel in ("services/cleanup.py", "services/thumb_warmup.py", "api/notify.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert re.search(r"^\s*_\w+_task\s*=\s*asyncio\.create_task\(", src, re.M), \
            f"{rel} 的 create_task 未持引用（GC 停摆风险，R2-P3-8）"
