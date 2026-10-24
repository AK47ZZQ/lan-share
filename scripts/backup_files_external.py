"""照片/文件外部备份执行器（v3.70）：主人插上目标盘后，一条命令完成第二份拷贝。

背景（2026-10-24 评审 #1）：全家照片单盘单拷贝是本项目唯一的存在性风险，
外部备份任务 09-13 起停用、替代方案一直"挂账"。本脚本把"挂账"变成
"插盘 + 一条命令"：

    python scripts/backup_files_external.py --dest E:\\lan-share-files
    python scripts/backup_files_external.py --dest E:\\lan-share-files --dry-run

策略：
  - robocopy /E 增量复制（只增不删，目标盘上多余文件永不动——宁可重复不可丢）；
  - 排除可再生/临时目录：.thumbs .chunks .zips .trash .orphaned；
    保留：全部用户文件、data/反馈、.backups（元数据备份，小）、.avatars；
  - 复制后清点目标侧文件数与总字节，写 manifest-<时间戳>.json 供对账
    （robocopy 退出码 >=8 才算失败；0-7 是成功/跳过的各种组合）。

安全闸（fail-closed）：
  - --dest 必须已存在（盘没插就报错，不建目录）；
  - 拒绝目标在 SHARE_ROOT 内部或就是 SHARE_ROOT；
  - 拒绝目标为盘符根（必须指定一个子目录，防误拷满盘）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import SHARE_ROOT  # noqa: E402

EXCLUDE_DIRS = [".thumbs", ".chunks", ".zips", ".trash", ".orphaned"]


def _validate_dest(dest: Path) -> str | None:
    if not dest.exists():
        return f"目标不存在：{dest}（盘插了吗？目录建了吗？）"
    if not dest.is_dir():
        return f"目标不是目录：{dest}"
    resolved = dest.resolve()
    share = SHARE_ROOT.resolve()
    if resolved == share:
        return f"目标就是数据目录本体：{resolved}"
    if share in resolved.parents:
        return f"目标落在数据目录内部：{resolved}"
    if resolved in share.parents:
        return f"目标不能是数据目录的祖先目录（会污染其同级）：{resolved}"
    if len(resolved.parts) <= 2:   # 例如 E:\ 或 C:\ —— 盘符根
        return f"拒绝写入盘符根，请在目标盘上指定一个子目录：{resolved}"
    return None


def _tally(root: Path) -> tuple[int, int]:
    files = bytes_ = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                files += 1
                bytes_ += p.stat().st_size
            except OSError:
                continue
    return files, bytes_


def main() -> int:
    ap = argparse.ArgumentParser(description="照片/文件外部备份（增量、不删目标）")
    ap.add_argument("--dest", required=True, help="目标目录（必须已存在，非盘符根）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将复制的文件，不落盘")
    args = ap.parse_args()

    dest = Path(args.dest)
    err = _validate_dest(dest)
    if err:
        print("拒绝执行：", err)
        return 2

    cmd = ["robocopy", str(SHARE_ROOT), str(dest), "/E", "/COPY:DAT", "/DCOPY:DAT",
           "/R:2", "/W:5", "/NP", "/TEE", "/NFL", "/NDL"]
    for d in EXCLUDE_DIRS:
        cmd += ["/XD", str(SHARE_ROOT / d)]
    if args.dry_run:
        cmd.append("/L")

    print("执行：", " ".join(cmd[:8]), "…")
    import subprocess
    r = subprocess.run(cmd)
    if r.returncode >= 8:
        print(f"robocopy 失败（退出码 {r.returncode}，>=8 才算失败）")
        return 1

    files, bytes_ = _tally(dest)
    manifest = {
        "kind": "lan-share-external-backup",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "src": str(SHARE_ROOT),
        "dest": str(dest.resolve()),
        "files": files,
        "bytes": bytes_,
        "excluded": EXCLUDE_DIRS,
        "robocopy_rc": r.returncode,
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        mp = dest / f"manifest-{time.strftime('%Y%m%d-%H%M%S')}.json"
        mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        print(f"完成：{files} 个文件 / {bytes_ / 1e9:.2f} GB，清单 {mp}")
    else:
        print(f"试运行（未落盘）：预计目标侧共 {files} 文件 / {bytes_ / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
