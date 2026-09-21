# -*- coding: utf-8 -*-
"""v1.1.7+111 发版三件套：审查加固版。APK 拷贝 + version.json + versions.json（插队首，先查重）。"""
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(r"C:\lan-share")
DIST = REPO / "data" / "App安装包"
APK_SRC = REPO / "mobile" / "build" / "apk" / "lanshare-release.apk"
APK_DST = DIST / "lanshare.apk"

VER = "1.1.7"
BUILD = 111
NOTES = ("稳定性加固版（6 轮审查 + 2 轮对抗性审查后的修复，界面无变化）：修复月视图"
         "顶部上滑加载期间返回全部时间线/切换筛选可能出现的照片墙空白；堵住该窗口期"
         "内长按选择照片可能导致批量删除选错对象的漏洞；连续多条提示条互相顶掉时不再"
         "误关新提示；新建待办的日期选择器定位到已选日期。建议更新。")

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
