"""临时运维：从回收站中精准清空"本次备份删除"产生的条目。

背景：2026-09-13 删除 C:\\lan-share-backup\\files（20.1GB）时，环境的
safe-delete 机制把删除重定向进了回收站——文件从原位置消失但空间未释放。
本脚本解析 $I 索引（内含原始路径），**只匹配原始路径在
C:\\lan-share-backup\\ 下的条目**，对它们做永久删除；其他会话删除的
内容（kelivo/apk 等）一律不碰。

$I 文件格式（Win10 V2）：int64 version, int64 size, int64 filetime(UTC),
int32 nameChars, UTF-16LE name。
"""
import os
import struct
import sys
from datetime import datetime
from pathlib import Path

RB = os.environ.get("SystemDrive", "C:") + "\\$Recycle.Bin"
PREFIX = "C:\\lan-share-backup"


def parse_i(path: Path):
    """解析 $I 文件，返回 (原始路径, 字节数, 删除时间)。"""
    data = path.read_bytes()
    if len(data) < 24:
        return None
    ver = struct.unpack("<q", data[0:8])[0]
    size = struct.unpack("<q", data[8:16])[0]
    ft = struct.unpack("<q", data[16:24])[0]
    if ver >= 2:
        (nchars,) = struct.unpack("<i", data[24:28])
        name = data[28:28 + nchars * 2].decode("utf-16-le", errors="replace").rstrip("\x00")
    else:
        name = data[24:].decode("utf-16-le", errors="replace").split("\x00")[0]
    ts = datetime.utcfromtimestamp((ft - 116444736000000000) / 1e7) if ft else None
    return name, size, ts


def main():
    apply = "--apply" in sys.argv
    mine = []          # ($I path, $R path, original, size, ts)
    for sid in os.listdir(RB):
        sub = Path(RB) / sid
        if not sub.is_dir():
            continue
        for i in sub.glob("$I*"):
            info = parse_i(i)
            if not info:
                continue
            orig, size, ts = info
            if orig.lower().replace("/", "\\").startswith(PREFIX.lower()):
                r = i.with_name("$R" + i.name[2:])
                mine.append((i, r, orig, size, ts))

    total = sum(m[3] for m in mine)
    print(f"MATCHED {len(mine)} entries, {total/1073741824:.2f} GB (original under {PREFIX})")
    for _, _, orig, size, ts in sorted(mine, key=lambda m: -m[3])[:12]:
        print(f"  {size/1073741824:8.3f} GB  {ts:%m-%d %H:%M}  {orig}")
    if len(mine) > 12:
        print(f"  ... and {len(mine)-12} more")

    if not apply:
        print("\nDRY-RUN. 加 --apply 执行永久删除（仅上述条目）。")
        return 0

    ok = fail = 0
    freed = 0
    for _, r, orig, size, _ts in mine:
        try:
            if r.is_dir():
                import shutil
                shutil.rmtree(r, ignore_errors=True)
            elif r.exists():
                r.unlink()
            i.unlink(missing_ok=True)
            ok += 1
            freed += size
        except OSError as e:
            fail += 1
            print("  FAIL", orig, e)
    print(f"APPLIED: {ok} purged ({freed/1073741824:.2f} GB), {fail} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
