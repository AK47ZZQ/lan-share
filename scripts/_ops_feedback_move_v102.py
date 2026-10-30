# -*- coding: utf-8 -*-
"""反馈流转：4 条待处理 → 已完成（改 frontmatter 状态/备注/更新）。"""
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\lan-share")
SRC = REPO / "data" / "反馈" / "待处理"
DST = REPO / "data" / "反馈" / "已完成"
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

NOTES = {
    "20260914041746-6c58": ("已修复",
        "v1.0.2+102 已修复：右侧月份栏从“按月过滤”改成“全时间线跳转”——点某个月"
        "直接定位到那个月的照片，不再锁死在单月里，跳完照样可以往上/往下翻别的月份；"
        "翻的时候高亮会跟着滚动走到当前月份，月份栏自身也会把当前月滚进视野；"
        "日期标题改为全宽色条加大加粗更醒目。跳转落点按您当前的筛选口径"
        "（类型/文件夹）精确推算，几千张照片也能秒级定位。App 更新后生效。"),
    "20260914042013-6e62": ("已实装",
        "v1.0.2+102 已实装：后台刷新（备份完成/新照片检测）从“清空整墙重拉”改为"
        "增量合并——内容没变化时界面纹丝不动（不再整屏转圈、不再重新加载图片），"
        "有变化也保持您的浏览位置不弹跳；翻页与月份跳转改为多页并行拉取；"
        "批量删除改 4 路并发，大批量删除约 4 倍速完成。App 更新后生效。"),
    "20260914042829-def1": ("已实装",
        "v1.0.2+102 已实装：①长按进入多选后手指别抬起，继续滑动经过的照片自动"
        "勾选（从已选中的照片起手则反向取消），滑到屏幕上/下边缘会自动滚动接着选；"
        "②“长按后被顶回列表顶部”已修——根因是后台自动刷新清空重拉，现在多选"
        "期间暂缓自动刷新，退出多选后自动补一次。App 更新后生效。"),
    "20260914042923-b7c1": ("已实装",
        "v1.0.2+102 已实装：根层此前没有接管系统返回手势，任何页面一拉就直接退到"
        "桌面。现在按层级处理：多选中→先退多选；非首页页签→先回首页；在首页"
        "2 秒内连续两次返回才会退出（第一次会提示“再返回一次退出”），不会再"
        "误触丢失浏览位置。App 更新后生效。"),
}

for fid, (status, note) in NOTES.items():
    src = SRC / f"{fid}.md"
    dst = DST / f"{fid}.md"
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines()
    out = []
    for ln in lines:
        if ln.startswith("状态:"):
            out.append(f"状态: {status}")
        elif ln.startswith("备注:"):
            out.append(f"备注: {note}")
        elif ln.startswith("更新:"):
            out.append(f"更新: {NOW}")
        else:
            out.append(ln)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    src.unlink()
    print(f"done: {fid} -> {status}")
print("all moved")
