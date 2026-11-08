"""Windows 文件 ACL 收紧单源（2026-11-09 值守批三抽出）。

消费者三处（.lanshare-pgpass / .lanshare-dburl / initdb 的 .pgpass.tmp），
同一安全语义——收紧到 SYSTEM + Administrators。历史上 QuoteArg 与
oneline 都因「两份实现」漂移过，第三个消费者出现时必须收编。

为什么 icacls 而不是 os.chmod：POSIX 语义不映射 Windows ACL（只碰只读位）。
为什么用 SID 不用组名：中文 Windows 组名本地化，Administrators 可能
解析不了（icacls 静默只授 SYSTEM——行为钉当场抓到过）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# *S-1-5-18 = SYSTEM、*S-1-5-32-544 = Administrators，全世界一样
_SYSTEM_SID = "*S-1-5-18:F"
_ADMINS_SID = "*S-1-5-32-544:F"


def lock_file_acl(f: Path) -> None:
    """断继承 + 只留 SYSTEM/Administrators 完全控制。失败只警告不抛。"""
    try:
        subprocess.run(
            ["icacls", str(f), "/inheritance:r",
             "/grant:r", _SYSTEM_SID, "/grant:r", _ADMINS_SID],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
        )
    except Exception as exc:
        # 自包含打印，不 import cli（cli 又 import 本模块 —— 反向依赖成环）
        print(f"WARN 收紧 {f.name} 权限失败（建议手动 icacls 处理）：{exc}")
