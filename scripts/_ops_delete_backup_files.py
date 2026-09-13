"""一次性运维：删除 C:\lan-share-backup\files（19G 用户文件副本）。

主人 2026-09-13 决策：暂停备份（等第二块 SSD 到位），删除原备份文件腾空间。
- 保留：db/（454KB dump）+ files-manifest.txt + pre-v35-20260829
- 删除：files/（7499 文件 / 20155777036 字节）
- 已确认：files/ 中"项目里没有"的目录（Hermes快传/apk/简历/logo图片/手机上传）
  与 照片 5775M 均为旧快照，主人确认不要了。

安全措施：
1. 删除前把完整文件清单（路径+大小+mtime）落到 audit 目录，事后可追溯；
2. 分批删除（每批 ≤200 个条目），逐批校验；
3. 只删 files/ 一个目录，绝不触碰 db/ 与上级目录。
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

TARGET = Path(r"C:\lan-share-backup\files")
AUDIT = Path(__file__).resolve().parent.parent / ".backup-audit"
MANIFEST = AUDIT / "files-inventory-before-delete.json"
BATCH = 200


def snapshot() -> int:
    """删除前登记完整清单（只读）。"""
    rows = []
    total = 0
    for dp, _dn, fn in os.walk(TARGET):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
                rows.append({
                    "p": os.path.relpath(p, TARGET),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
                total += st.st_size
            except OSError:
                pass
    AUDIT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(
        {
            "target": str(TARGET),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file_count": len(rows),
            "total_bytes": total,
            "manifest_ref": "generated=2026-09-12T23:30:02 files=7499 bytes=20155777036",
            "files": rows,
        },
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SNAPSHOT: {len(rows)} files, {total} bytes -> {MANIFEST.name}")
    return len(rows)


def delete_batched() -> None:
    """自底向上分批删空目录树（先文件后目录），逐批打印。"""
    # 收集所有条目，深度降序（先删最深的）
    entries = []
    for dp, dn, fn in os.walk(TARGET, topdown=False):
        for f in fn:
            entries.append(Path(dp) / f)
        for d in dn:
            entries.append(Path(dp) / d)
    entries.sort(key=lambda p: len(p.parts), reverse=True)

    total = len(entries)
    done = 0
    for i in range(0, total, BATCH):
        chunk = entries[i:i + BATCH]
        for p in chunk:
            try:
                if p.is_dir() and not p.is_symlink():
                    p.rmdir()
                else:
                    p.unlink()
                done += 1
            except FileNotFoundError:
                done += 1
            except OSError as e:
                print(f"  SKIP {p}: {e}")
        print(f"  batch {i // BATCH + 1}: {done}/{total}")


def main():
    if not TARGET.exists():
        print("TARGET_ALREADY_GONE:", TARGET)
        return 0

    if "--confirm-delete" not in sys.argv:
        print("DRY-RUN. 加 --confirm-delete 才真正删除。")
        snapshot()
        return 0

    snapshot()
    # 纵深保护：路径必须精确匹配，且父目录必须是备份根
    if TARGET.name != "files" or TARGET.parent.name != "lan-share-backup":
        print("ABORT: 路径校验未通过")
        return 9

    print(f"DELETING {TARGET} ...")
    delete_batched()

    if TARGET.exists():
        left = list(TARGET.rglob("*"))
        print(f"REMAINING: {len(left)} entries")
        if left:
            return 1
    print("OK-DELETED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
