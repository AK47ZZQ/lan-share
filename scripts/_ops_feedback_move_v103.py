# -*- coding: utf-8 -*-
"""反馈 31cd 流转：进行中 → 已完成（已修复）+ 用户视角备注。"""
import shutil
from pathlib import Path

REPO = Path(r"C:\Users\<你的用户名>\Desktop\AI Agent 共享空间\协作产物\lan-share")
SRC = REPO / "data" / "反馈" / "进行中" / "20260914213301-31cd.md"
DST = REPO / "data" / "反馈" / "已完成" / "20260914213301-31cd.md"

text = SRC.read_text(encoding="utf-8")
text = text.replace("状态: 已提交", "状态: 已修复")
note = (
    "备注: v1.0.3+103 已修复（配合服务端 v3.54）：加载慢的大头在服务端——"
    "上传完的照片缩略图都是你点开时才现场生成，视频最坏一张要 25 秒，8 路请求全部堵在"
    "生成队列里整屏转圈。现在服务端会按每 30 秒一批、新照片优先，提前把缩略图备好，"
    "浏览时直接秒开。滑动卡是 1.0.2 的两个新问题：滚动跨月时高亮刷新把整页重算了一遍，"
    "已收敛成只刷新时间轴本身；时间轴毛玻璃特效每帧都在实时模糊，已换成几乎零成本的"
    "半透明底。另外修了\"后台进新照片时屏幕里已加载的图被打回转圈\"（列表项现在按路径"
    "复用）和看过的照片被反复解码的问题（缓存扩容 4 倍）。App 更新 + 服务端已重启生效。"
)
lines = text.splitlines()
out = []
for ln in lines:
    if ln.startswith("备注:") or ln.startswith("更新:"):
        continue
    out.append(ln)
text = "\n".join(out) + f"\n{note}\n更新: 2026-09-14 23:30:00\n"
DST.write_text(text, encoding="utf-8")
SRC.unlink()
print("moved:", DST.exists(), "| src removed:", not SRC.exists())
