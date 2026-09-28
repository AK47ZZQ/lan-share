"""全局配置：env > 默认值。（v3 M0 自 server.py 机械搬运；PROJECT_ROOT 相应上移一级）"""

import os
from pathlib import Path

SHARE_ROOT = Path(os.environ.get("SHARE_ROOT", "C:/lan-share/data"))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SHARE_ROOT / ".meta.db"

MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024   # 数据盘写前余量闸（PG 同盘，写满=打挂库）
TMP_MAX_AGE_DAYS = 7
CLEANUP_INTERVAL = 3600  # 1 hour

CHUNK_SIZE = 5 * 1024 * 1024          # 分块上传 5MB/块
UPLOAD_SESSION_TTL = 3 * 86400        # 上传会话 3 天过期（断点续传窗口）
TRASH_DAYS = 30                       # 回收站保留天数
LOG_ARCHIVE_MAX_AGE_DAYS = 14         # NSSM 轮转出的日志归档保留天数（app/services/log_sweep.py）

# ── 备份编排与水位告警（B5）──────────────────────────────
BACKUP_DIR = SHARE_ROOT / ".backups"   # 备份落点（数据目录内，随数据一起被网盘级保护）
BACKUP_KEEP = 7                        # 保留最近 N 份（dump+元数据快照各算一份策略）
BACKUP_HOUR = 4                        # 每日备份时刻（小时；清理循环每小时醒一次到点补跑）
# PG 客户端工具回退目录：pg_dump/pg_restore 不保证在服务 PATH 上（NSSM 服务
# 环境随重启刷新，PATH 漂移是本项目实录过的坑）——which() 找不到时按此目录
# 兜底（Windows 部署默认安装位，env.bat LANSHARE_PGDUMP 同源；可用环境变量覆盖）
PG_BIN_FALLBACK = os.environ.get("LANSHARE_PG_BIN", "C:/lan-share/env/pgsql-bin")
DISK_WATERMARK_PCT = 85.0              # 磁盘已用百分比告警阈值
ALERT_COOLDOWN = 6 * 3600              # 同类告警冷却期（防轰炸）
FEEDBACK_MAX_ATTACHMENT_MB = 25        # 反馈附件单件上限（other·0069：12→25，config 化）
ZIP_TTL = 15 * 60                     # zip 打包下载票据有效期（秒）

SESSION_TTL = 30 * 86400          # 30 天
SESSION_RENEW_AFTER = 86400       # 滑动续期：距上次刷新超过 24h 才写库
COOKIE_NAME = "ls_session"
AUDIT_KEEP = 10_000
# 安全类审计动作独立保留（v3.67.2 移植自分叉批 P2-4）：登录失败/取件/爆破尝试
# 是取证记录，与普通动态流同环时匿名失败流量约 4h 可把它们全部挤出环外——
# 攻击者可主动冲掉自己留下的证据
AUDIT_SECURITY_KEEP = 50_000
DEVICE_TOKEN_TTL = 90 * 86400     # App 设备 Token：90 天滑动过期（APP-DESIGN §3）
DEVICE_TOKEN_MAX = 20             # 单账号在用设备上限
API_RATE_LIMIT = 120              # /api/* 每分钟每 IP（交互类：浏览/列表/缩略图）
# 上传专用桶（bug·882a 根因修复）：照片备份一批就是几十上百次
# init/chunk/finish（或直传 multipart），此前全部计入 api:lan 120/min——
# 备份过程中一旦打满，同一出口 IP 的**所有** /api/* 都被 429（照片墙/文件页
# 报「加载失败」、SSE 断连退化成轮询继续被拒）。上传端点已全程鉴权
# （require_user）+ 受配额/分片会话双上限约束，与交互桶隔离是正确切法：
# 备份不再挤掉浏览，浏览器刷列表也挤不掉备份。chunk 仍按块单独豁免（见 asgi）。
UPLOAD_RATE_LIMIT = 600           # /api/upload*（init/state/finish/直传）每分钟每 IP
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
# dav/media 桶（v3.67.2 移植自分叉批 P1）：原"未列举即不限速"分支——/dav 的
# scrypt Basic 校验 ~377ms/次，匿名 ~13 req/s 即可长期占满线程池；批量冷缩略图
# 可占满渲染。DAV 客户端每请求都带 Basic，LAN 取宽值；公网出口恒 127.0.0.1
# 全体共享一个桶，洪泛被 cap 在每分钟 DAV_RATE_LIMIT_PUB 次
DAV_RATE_LIMIT = 300              # /dav 每分钟每 IP
DAV_RATE_LIMIT_PUB = 600          # /dav 公网侧每分钟
DAV_VERIFY_PER_MIN = 120          # /dav Basic 认证 KDF 前的每 IP 校验预算（第二层）
MEDIA_RATE_LIMIT = 240            # /thumb /preview /files 每分钟每 IP
MEDIA_RATE_LIMIT_PUB = 1200       # 同上 公网侧（公网省流依赖缩略图，取宽值）
MEDIA_SEM_SIZE = 4                # 渲染专用池线程数（2026-09-28 实测定标：全库最大 12 张 363MB 样本，4 线程 0.88s≈13.6 图/s，8 线程 0.66s 仅 +33% 而内存/争用翻倍——4 保留 ~75% 峰值吞吐且近线性至 4，留余量给在线服务）

# ── 大文件上传（v3.6）────────────────────────────────────
MAX_WEBDAV_SIZE = 20 * 1024 * 1024 * 1024   # WebDAV PUT 上限 20GB（流式写盘，PC 互传挂盘用）
MAX_CHUNKED_SIZE = 64 * 1024 * 1024 * 1024  # 分片上传总大小硬上限 64GB
CHUNKS_TOTAL_CAP = 128 * 1024 * 1024 * 1024  # 全家在途分片总量上限（数据盘与 PG 同盘）
_GB = 1024 * 1024 * 1024

# 分片协议上限——单一事实源（07 审查 P0）：服务端 chunk_size_for 的输出、
# init/state 响应里的 max_chunk_size、移动端两引擎的防御 clamp、Caddyfile 的
# "单块 ≤64MB" 注释，全部以它为准。改阶梯只许改 chunk_size_for，新值不得超过
# 本常量；客户端收紧/放宽 clamp 只许引用本值（经响应下发），不许写死数字。
MAX_CHUNK_SIZE = 64 * 1024 * 1024


def chunk_size_for(total_size: int) -> int:
    """按文件大小自适应分片——纯函数，init/state/chunk/finish 四处一致（断点续传依赖）。

    10GB@5MB=2048 个请求；自适应后 32MB=320 个，减少 HTTP 往返开销。
    输出契约：0 < 返回值 ≤ MAX_CHUNK_SIZE（test_chunk_contract 钉死）。
    """
    if total_size <= 2 * _GB:
        return CHUNK_SIZE            # 5MB
    if total_size <= 5 * _GB:
        return 16 * 1024 * 1024      # 16MB
    if total_size <= 10 * _GB:
        return 32 * 1024 * 1024      # 32MB
    return MAX_CHUNK_SIZE            # 64MB
