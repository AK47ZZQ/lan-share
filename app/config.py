"""全局配置：env > 默认值。（v3 M0 自 server.py 机械搬运；PROJECT_ROOT 相应上移一级）"""

import os
from pathlib import Path

SHARE_ROOT = Path(os.environ.get("SHARE_ROOT", "C:/lan-share/data"))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SHARE_ROOT / ".meta.db"

MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
TMP_MAX_AGE_DAYS = 7
CLEANUP_INTERVAL = 3600  # 1 hour

CHUNK_SIZE = 5 * 1024 * 1024          # 分块上传 5MB/块
UPLOAD_SESSION_TTL = 3 * 86400        # 上传会话 3 天过期（断点续传窗口）
TRASH_DAYS = 30                       # 回收站保留天数
ZIP_TTL = 15 * 60                     # zip 打包下载票据有效期（秒）

SESSION_TTL = 30 * 86400          # 30 天
SESSION_RENEW_AFTER = 86400       # 滑动续期：距上次刷新超过 24h 才写库
COOKIE_NAME = "ls_session"
AUDIT_KEEP = 10_000
DEVICE_TOKEN_TTL = 90 * 86400     # App 设备 Token：90 天滑动过期（APP-DESIGN §3）
DEVICE_TOKEN_MAX = 20             # 单账号在用设备上限
API_RATE_LIMIT = 120              # /api/* 每分钟每 IP
LOGIN_RATE_LIMIT = 10             # /api/login 每分钟每 IP
SHARE_RATE_LIMIT = 30             # 分享取件 每分钟每 IP
LOGIN_MAX_FAILS = 5               # 连续失败次数
LOGIN_LOCK_SECONDS = 15 * 60      # 账号锁定时长
TICKET_TTL = 10 * 60              # 分下载票据有效期（秒）
# 公网侧全体共享一个出口 IP 桶（隧道恒 127.0.0.1），按 LAN 阈值会让单人刷满
# 桶卡死全家（R1-P2-3）；爆破防线由按账号锁定兜底，IP 桶只防洪水
LOGIN_RATE_LIMIT_PUB = 30         # /api/login 公网侧每分钟
API_RATE_LIMIT_PUB = 600          # /api/* 公网侧每分钟
AUTHCHECK_RATE_LIMIT = 600        # /authcheck（forward_auth 子请求 + 外部直打）每分钟

# ── 大文件上传（v3.6）────────────────────────────────────
MAX_WEBDAV_SIZE = 20 * 1024 * 1024 * 1024   # WebDAV PUT 上限 20GB（流式写盘，PC 互传挂盘用）
MAX_CHUNKED_SIZE = 64 * 1024 * 1024 * 1024  # 分片上传总大小硬上限 64GB
_GB = 1024 * 1024 * 1024


def chunk_size_for(total_size: int) -> int:
    """按文件大小自适应分片——纯函数，init/state/chunk/finish 四处一致（断点续传依赖）。

    10GB@5MB=2048 个请求；自适应后 32MB=320 个，减少 HTTP 往返开销。
    """
    if total_size <= 2 * _GB:
        return CHUNK_SIZE            # 5MB
    if total_size <= 5 * _GB:
        return 16 * 1024 * 1024      # 16MB
    if total_size <= 10 * _GB:
        return 32 * 1024 * 1024      # 32MB
    return 64 * 1024 * 1024          # 64MB
