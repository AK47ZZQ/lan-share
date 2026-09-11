// 全量 API 客户端·类型定义（v3.48 从 api.ts 按域拆分；门面仍在 ../api.ts）
export interface Entry {
  name: string; path: string; is_dir: boolean; is_private_root?: boolean
  size: number | null; size_human: string; modified: string; ext: string | null
  abs_path?: string  // 服务器真实存储路径（"复制路径"用，9.6-P1-3）
}
export interface Me { username: string; role: 'admin' | 'member' | 'guest'; quota_bytes: number | null; totp_enabled?: boolean; totp_rc_left?: number; nickname?: string; avatar_path?: string }
export interface TrashItem { id: number; name: string; orig_path: string; size_human: string; deleted_at: string; days_left: number }
export interface ShareItem {
  token: string; owner: string; target: string | null; path: string; name: string; size: number | null
  size_human: string; expires_at: string; expires_in: string
  has_password: boolean; password: string | null; downloads: number; max_downloads: number | null; created_at: string
}
export interface ShareHistoryRow { ts: number; ip: string; action: string }
export interface ShareMeta { name: string; size_human: string; expires_in: string; needs_password: boolean; max_downloads: number | null; downloads: number }
export interface AdminUser { id: number; username: string; nickname?: string; avatar_path?: string; role: string; is_active: boolean; quota_bytes: number | null; quota_human: string; created_at: string; webdav_set?: boolean; totp_enabled?: boolean }

export interface Grant { id: number; user_id: number; path: string; can_read: boolean; can_write: boolean; expires_at: number | null }
export interface ActivityItem { ts: number; username: string; action: string; target: string; detail: string; count: number }
export interface DiskInfo { total: string; used: string; free: string; total_human: string; used_human: string; free_human: string; total_bytes: number; used_bytes: number; free_bytes: number; share_size: string; share_size_human: string; warning: boolean }
export interface Member { id: number; username: string; role: string }
export interface DeviceInfo { id: number; name: string; created_at: number; last_seen: number; current: boolean }
export interface NotificationItem { id: number; type: string; title: string; body: string | null; link: string | null; created_at: number; read: boolean }
export interface BroadcastItem {
  id: string; title: string; body: string | null; target_count: number
  status: 'draft' | 'scheduled' | 'sent'; scheduled_at: number | null
  sent_at: number | null; withdrawn_at: number | null; created_at: number
  created_by: string; unread: number; read: number
}
export interface PhotoItem { path: string; name: string; taken: string; month: string; uploader: string; thumb: string }
export interface PhotoFacets { months: { month: string; count: number }[]; uploaders: { name: string; count: number }[] }
export interface Album { id: string; title: string; month?: string; created_by?: string; count?: number; cover?: string }

export interface TrafficDay { date: string; bytes: number }
export interface TrafficInfo {
  today_bytes: number; month_bytes: number; free_tier_bytes: number
  days: TrafficDay[]
}
export interface ConsoleOverview {
  users: { total: number; active: number; admins: number; guests: number }
  photos: { count: number; size: number }
  shares: { total: number; active: number; downloads: number }
  trash: { count: number; size: number }
  blobs: { count: number; size: number }
  sessions: { active: number }
  devices: { active: number }
  disk: { used_pct: number; free_gb: number; total_gb: number; free_bytes: number; total_bytes: number; used_bytes: number }
  app_latest: { version: string; build: number } | null
  traffic: TrafficInfo
  server_time: number
}
export interface LargeFileItem { path: string; size: number; mtime: number }
export interface DupGroup { size: number; paths: string[]; wasted: number }
export interface AuditItem { ts: number; ip: string; action: string; target: string; detail: string; username: string }
export interface AdminSession { token_hash: string; username: string; created_at: number; expires_at: number; ip: string; ua: string }
export interface AdminDevice { id: number; username: string; name: string; created_at: number; last_seen: number | null; expires_at: number }
export interface Announcement { id: number; title: string; body: string | null; created_by: string; created_at: number }
export interface AdminNotificationItem { id: number; username: string; type: string; title: string; body: string | null; link: string | null; created_at: number; read_at: number | null; broadcast_id: string | null }
