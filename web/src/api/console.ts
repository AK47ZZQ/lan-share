// 管理台域（v3.48 从 api.ts 按域拆分）：总览/治理/审计/会话设备/公告与站内
// 通知管理/广播/成员与权限/邀请/授权矩阵/webhook/健康日报/公网地址/数据导出。
import { handle, jpost } from './core'
import type {
  AdminDevice, AdminNotificationItem, AdminSession, AdminUser, Announcement,
  AuditItem, BroadcastItem, ConsoleOverview, DupGroup, Grant, LargeFileItem,
  MetricsSnapshot,
} from './types'

export const consoleApi = {
  // 管理台（v3.39）：总览 / 治理 / 会话与设备
  adminOverview: () => fetch('/api/admin/overview').then(r => handle<ConsoleOverview>(r)),
  largeFiles: (limit = 50) => fetch(`/api/admin/large-files?limit=${limit}`).then(r => handle<{ items: LargeFileItem[]; total_files: number; total_size: number }>(r)),
  duplicates: (minSize: number, limit = 50) => fetch(`/api/admin/duplicates?min_size=${minSize}&limit=${limit}`).then(r => handle<{ groups: DupGroup[]; group_count: number }>(r)),
  auditLog: (limit = 100, action = '') => fetch(`/api/admin/audit?limit=${limit}${action ? `&action=${encodeURIComponent(action)}` : ''}`).then(r => handle<{ items: AuditItem[]; actions: string[] }>(r)),
  adminSessions: () => fetch('/api/admin/sessions').then(r => handle<{ items: AdminSession[] }>(r)),
  revokeAdminSession: (tokenHash: string) => fetch(`/api/admin/sessions/${encodeURIComponent(tokenHash)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminDevices: () => fetch('/api/admin/devices').then(r => handle<{ items: AdminDevice[] }>(r)),
  revokeAdminDevice: (id: number) => fetch(`/api/admin/devices/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),

  // 公告栏 + 站内通知管理 + 会话治理（v3.40）
  announcements: () => fetch('/api/announcements').then(r => handle<{ items: Announcement[] }>(r)),
  createAnnouncement: (title: string, body: string) => jpost('/api/admin/announcements', { title, body }).then(r => handle<{ ok: boolean }>(r)),
  deleteAnnouncement: (id: number) => fetch(`/api/admin/announcements/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminNotifications: (username = '', limit = 100) => {
    const q = new URLSearchParams({ limit: String(limit) })
    if (username) q.set('username', username)
    return fetch(`/api/admin/notifications?${q}`).then(r => handle<{ items: AdminNotificationItem[] }>(r))
  },
  deleteAdminNotification: (id: number) => fetch(`/api/admin/notifications/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  purgeBroadcast: (id: string) => fetch(`/api/admin/notifications/broadcast/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean; notifications_removed: number }>(r)),
  kickUser: (uid: number, scope: 'all' | 'web' | 'app') => jpost(`/api/admin/users/${uid}/kick`, { scope }).then(r => handle<{ ok: boolean; sessions: number; devices: number }>(r)),
  purgeSessions: () => fetch('/api/admin/sessions', { method: 'DELETE' }).then(r => handle<{ ok: boolean; revoked: number }>(r)),
  // v0.20 手动广播：站内通知逐人 + 已配置 webhook 同步推
  adminNotify: (title: string, body: string, userIds?: number[]) =>
    jpost('/api/admin/notify', { title, body, ...(userIds?.length ? { user_ids: userIds } : {}) })
      .then(r => handle<{ ok: boolean; sent: number; webhook: boolean; broadcast_id: string }>(r)),

  // 通知管理（9.6-P2-14）：发送记录/撤回/提醒未读/草稿定时
  sentBroadcasts: () => fetch('/api/admin/notifications/sent').then(r => handle<{ items: BroadcastItem[] }>(r)),
  withdrawBroadcast: (id: string) =>
    jpost(`/api/admin/notifications/broadcast/${encodeURIComponent(id)}/withdraw`, {}).then(r => handle<{ ok: boolean; removed: number }>(r)),
  remindBroadcast: (id: string) =>
    jpost(`/api/admin/notifications/broadcast/${encodeURIComponent(id)}/remind`, {}).then(r => handle<{ ok: boolean; unread: number }>(r)),
  saveDraft: (title: string, body: string, userIds: number[] | null, scheduledAt: number | null) =>
    jpost('/api/admin/notifications/draft', { title, body, user_ids: userIds, scheduled_at: scheduledAt })
      .then(r => handle<{ ok: boolean; id: string }>(r)),
  sendDraft: (id: string) =>
    jpost(`/api/admin/notifications/draft/${encodeURIComponent(id)}/send`, {}).then(r => handle<{ ok: boolean; sent: number }>(r)),
  deleteDraft: (id: string) =>
    fetch(`/api/admin/notifications/draft/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),

  // webhook / 健康日报 / 公网地址
  getWebhook: () => fetch('/api/admin/webhook').then(r => handle<{ url: string }>(r)),
  setWebhook: (url: string) => jpost('/api/admin/webhook', { url }).then(r => handle<{ ok: boolean; url: string }>(r)),
  testWebhook: () => jpost('/api/admin/webhook/test', {}).then(r => handle<{ ok: boolean }>(r)),
  // 每日健康日报（磁盘/备份/证书/隧道 → Webhook）
  getHealthReport: () => fetch('/api/admin/healthreport').then(r => handle<{ enabled: boolean; time: string; backup_dest: string; webhook_set: boolean }>(r)),
  setHealthReport: (enabled: boolean, time: string) => jpost('/api/admin/healthreport', { enabled, time }).then(r => handle<{ ok: boolean; enabled: boolean; time: string }>(r)),
  testHealthReport: () => jpost('/api/admin/healthreport/test', {}).then(r => handle<{ ok: boolean; sent: boolean | null; warn: boolean; report: string; webhook_set: boolean }>(r)),
  // 运行指标（v3.52 B5；backup.verified = 兜底 dump 内联验证状态）
  getMetrics: () => fetch('/api/metrics').then(r => handle<MetricsSnapshot>(r)),
  getPublicUrl: () => fetch('/api/public-url').then(r => handle<{ public_base_url: string; public_base_url_alt: string }>(r)),
  setPublicUrl: (url: string, urlAlt = '') => jpost('/api/admin/public-url', { url, url_alt: urlAlt }).then(r => handle<{ ok: boolean }>(r)),

  // 成员与权限 / 邀请 / 授权矩阵
  adminUsers: () => fetch('/api/admin/users').then(r => handle<AdminUser[]>(r)),
  adminPatchUser: (uid: number, body: { is_active?: boolean; quota_bytes?: number | null; role?: string }) =>
    fetch(`/api/admin/users/${uid}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteUser: (uid: number) => fetch(`/api/admin/users/${uid}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminSetUsername: (uid: number, new_username: string) =>
    jpost(`/api/admin/users/${uid}/username`, { value: new_username }).then(r => handle<{ ok: boolean; username: string }>(r)),
  adminSetPassword: (uid: number, value: string) =>
    jpost(`/api/admin/users/${uid}/password`, { value }).then(r => handle<{ ok: boolean }>(r)),
  adminTotpDisable: (uid: number) =>
    jpost(`/api/admin/users/${uid}/totp-disable`, {}).then(r => handle<{ ok: boolean }>(r)),
  adminSetWebdavPwd: (uid: number, value: string) =>
    jpost(`/api/admin/users/${uid}/webdav-password`, { value }).then(r => handle<{ ok: boolean; set: boolean }>(r)),
  adminCreateInvite: (role: string, hours: number) => jpost('/api/admin/invites', { role, hours }).then(r => handle<{ code: string }>(r)),
  adminInvites: () => fetch('/api/admin/invites').then(r => handle<{ code: string; role: string; expired: boolean; used: boolean }[]>(r)),
  adminDeleteInvite: (code: string) => fetch(`/api/admin/invites/${encodeURIComponent(code)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminGrants: (user_id?: number) => fetch(`/api/admin/grants${user_id ? `?user_id=${user_id}` : ''}`).then(r => handle<Grant[]>(r)),
  adminCreateGrant: (user_id: number, path: string, can_read: boolean, can_write: boolean, expires_hours?: number) =>
    jpost('/api/admin/grants', { user_id, path, can_read, can_write, expires_hours }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteGrant: (gid: number) => fetch(`/api/admin/grants/${gid}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),

  // 数据导出（管理）：成员数据足迹 zip
  exportMember: (username: string) => jpost('/api/admin/export', { username }).then(r => handle<{ ok: boolean; url: string; name: string; files: number; bytes: number }>(r)),
  // 管理相册（admin 建删）
  adminAddAlbum: (title: string, month: string) => jpost('/api/admin/albums', { title, month }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteAlbum: (id: string) => fetch(`/api/admin/albums/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
}
