# -*- coding: utf-8 -*-
"""Release helper: update data/App安装包 三件套 (APK copy + version.json + versions.json).
Usage: python scripts/_release_apk_meta.py <version> <build> "<notes>"
Reads sha256/size from the actually built APK (mobile/build/apk/lanshare-release.apk).
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APK_SRC = REPO / "mobile" / "build" / "apk" / "lanshare-release.apk"
DIST = REPO / "data" / "App安装包"
VERSION_JSON = DIST / "version.json"
VERSIONS_JSON = DIST / "versions.json"
DATE = __import__("datetime").date.today().isoformat()


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    version, build, notes = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    if not APK_SRC.exists():
        sys.exit(f"APK not found: {APK_SRC}")
    size = APK_SRC.stat().st_size
    digest = sha256_of(APK_SRC)

    # 1. APK -> 分发目录
    shutil.copyfile(APK_SRC, DIST / "lanshare.apk")

    # 2. version.json（最新版，检查更新弹窗数据源）
    version_json = {
        "version": version,
        "build": build,
        "filename": "lanshare.apk",
        "notes": notes,
        "sha256": digest,
        "size": size,
        "date": DATE,
    }
    VERSION_JSON.write_text(
        json.dumps(version_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 3. versions.json（更新历史，prepend 新条目）
    history = json.loads(VERSIONS_JSON.read_text(encoding="utf-8"))
    items = history.get("items", [])
    if any(it.get("version") == version and it.get("build") == build for it in items):
        sys.exit(f"{version}+{build} already in versions.json — build 号已被占用，重发必须再 +1")
    items.insert(0, version_json)
    VERSIONS_JSON.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"OK: {version}+{build}  {size}B  sha256={digest[:8]}…  history={len(items)} 条")


if __name__ == "__main__":
    main()
