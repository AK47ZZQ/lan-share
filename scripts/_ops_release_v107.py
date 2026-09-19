# -*- coding: utf-8 -*-
"""v1.1.3+107 发版三件套：APK 拷贝 + version.json + versions.json（插队首，先查重）。"""
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(r"C:\Users\<你的用户名>\Desktop\AI Agent 共享空间\协作产物\lan-share")
DIST = REPO / "data" / "App安装包"
APK_SRC = REPO / "mobile" / "build" / "apk" / "lanshare-release.apk"
APK_DST = DIST / "lanshare.apk"

VER = "1.1.3"
BUILD = 107
NOTES = ("修复照片墙时间线跳转在真机上仍会弹回顶部的问题（本轮找到真正根因："
         "跳转过程中界面结构重建导致定位作废），点右缘月份轴现在稳稳落到目标月、"
         "照片正常加载、跳完可自由翻。效率舱待办修复：今天截止的待办此前被错分进"
         "「未来 7 天」，顶部「今日待办」一直显示 0/0、「今天」分组一直空——现在"
         "正确计入今天。文件多选新增重命名：勾选单个文件（或文件夹）后点右上角"
         "「更多」即可重命名。App 更新即可，无需服务端改动。")

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
