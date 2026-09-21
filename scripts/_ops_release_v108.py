# -*- coding: utf-8 -*-
"""v1.1.4+108 发版三件套：APK 拷贝 + version.json + versions.json（插队首，先查重）。"""
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(r"C:\lan-share")
DIST = REPO / "data" / "App安装包"
APK_SRC = REPO / "mobile" / "build" / "apk" / "lanshare-release.apk"
APK_DST = DIST / "lanshare.apk"

VER = "1.1.4"
BUILD = 108
NOTES = ("照片墙时间线整体重构：点右缘月份轴现在**直达该月的月视图**——只拉当月"
         "数据，秒级落地、照片立即可见，不再卡顿、不再加载不出、不再弹回顶部；"
         "看完整月后继续往下滑会自动接上更早的月份，顶部横幅或月份轴「全部」"
         "随时回到全时间线。修复：完成待办后「已完成 xxx」提示条一直悬浮在底部"
         "不消失的问题（连带修复所有轻提示的同类隐患）。App 更新即可，无需服务端"
         "改动。")

sha = hashlib.sha256(APK_SRC.read_bytes()).hexdigest()
size = APK_SRC.stat().st_size
entry = {"version": VER, "build": BUILD, "filename": "lanshare.apk",
         "notes": NOTES, "sha256": sha, "size": size, "date": "2026-09-19"}

shutil.copyfile(APK_SRC, APK_DST)

(DIST / "version.json").write_text(
    json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

vp = DIST / "versions.json"
items = json.loads(vp.read_text(encoding="utf-8"))["items"]
items = [x for x in items if not (x.get("version") == VER and x.get("build") == BUILD)]
items.insert(0, entry)
vp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n",
              encoding="utf-8")

print("OK", sha[:16], size, "items:", len(items))
