# -*- coding: utf-8 -*-
"""v1.0.2+102 发版三件套：APK 已拷贝，更新 version.json + versions.json（插队首，先查重）。"""
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(r"C:\Users\<你的用户名>\Desktop\AI Agent 共享空间\协作产物\lan-share")
DIST = REPO / "data" / "App安装包"
APK_SRC = REPO / "mobile" / "build" / "apk" / "lanshare-release.apk"
APK_DST = DIST / "lanshare.apk"

VER = "1.0.2"
BUILD = 102
NOTES = ("照片墙体验更新：右侧月份栏点一下就能跳到那个月的照片，跳完还能继续上下翻，"
         "高亮会跟着你滚动走，日期标题更醒目；多选照片时按住别松手继续滑，经过的照片自动勾选，"
         "滑到屏幕边缘还会自动滚动接着选，长按多选也不会再跳回顶部；照片多了浏览更流畅、"
         "批量删除更快；返回手势不会再突然退出 App（先回上一级，首页连按两次才退出）。")

sha = hashlib.sha256(APK_SRC.read_bytes()).hexdigest()
size = APK_SRC.stat().st_size
entry = {"version": VER, "build": BUILD, "filename": "lanshare.apk",
         "notes": NOTES, "sha256": sha, "size": size, "date": "2026-09-14"}

# 1) 覆盖分发 APK
shutil.copyfile(APK_SRC, APK_DST)

# 2) version.json（最新版）
(DIST / "version.json").write_text(
    json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# 3) versions.json（新条目插队首；先查重防重复条目——09-14 教训）
vp = DIST / "versions.json"
items = json.loads(vp.read_text(encoding="utf-8"))["items"]
items = [x for x in items if not (x.get("version") == VER and x.get("build") == BUILD)]
items.insert(0, entry)
vp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n",
              encoding="utf-8")

print("OK", sha[:16], size, "items:", len(items))
