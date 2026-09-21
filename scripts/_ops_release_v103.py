# -*- coding: utf-8 -*-
"""v1.0.3+103 发版三件套：APK 已拷贝，更新 version.json + versions.json（插队首，先查重）。"""
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(r"C:\lan-share")
DIST = REPO / "data" / "App安装包"
APK_SRC = REPO / "mobile" / "build" / "apk" / "lanshare-release.apk"
APK_DST = DIST / "lanshare.apk"

VER = "1.0.3"
BUILD = 103
NOTES = ("照片墙性能更新：上传完再打开照片墙，缩略图由服务端提前备好，浏览几乎秒开，"
         "不再整屏转圈；后台有新照片进来时，屏幕里已加载的照片不会再被打回加载圈；"
         "快速滑动更顺滑（滚动跨月不再整页重算、时间轴去掉毛玻璃特效、看过的照片驻留内存"
         "不再重复解码）；翻页流量减少六成。配合服务端 v3.54 生效，App 更新即可。")

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
