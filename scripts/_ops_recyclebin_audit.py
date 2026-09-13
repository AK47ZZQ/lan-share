"""临时运维：统计回收站内容（按 SID 分组），确认删除数据去向。"""
import os

RB = os.environ.get("SystemDrive", "C:") + "\\$Recycle.Bin"

for sid in os.listdir(RB):
    p = os.path.join(RB, sid)
    try:
        n = sum(len(f) for _, _, f in os.walk(p))
        sz = sum(os.path.getsize(os.path.join(dp, f))
                 for dp, _, fn in os.walk(p) for f in fn)
        print(f"{sid}: {n} files, {sz/1073741824:.2f} GB")
    except OSError as e:
        print(sid, "err", e)

# 找出最大的几个 $R 文件（回收站真实数据体）
rows = []
for sid in os.listdir(RB):
    sub = os.path.join(RB, sid)
    for dp, _dn, fn in os.walk(sub):
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                rows.append((os.path.getsize(fp), fp))
            except OSError:
                pass
rows.sort(reverse=True)
print("\nTop 10:")
for sz, fp in rows[:10]:
    print(f"{sz/1073741824:8.2f} GB  {fp}")
