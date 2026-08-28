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
API_RATE_LIMIT = 120              # /api/* 每分钟每 IP
LOGIN_RATE_LIMIT = 10             # /api/login 每分钟每 IP
SHARE_RATE_LIMIT = 30             # 分享取件 每分钟每 IP
LOGIN_MAX_FAILS = 5               # 连续失败次数
LOGIN_LOCK_SECONDS = 15 * 60      # 账号锁定时长
TICKET_TTL = 10 * 60              # 分下载票据有效期（秒）
