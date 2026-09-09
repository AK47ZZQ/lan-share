// 全量 API 客户端（cookie 会话）
import { confirmDlg } from './ui'
import { previewAs } from './previewAs'

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

async function handle<T>(r: Response): Promise<T> {
  if (r.status === 401) {
    // 会话过期：整页跳登录（带 next 回跳）。公开页（/s/ 分享、/up 直传、/login）
    // 与登录态探测 /api/auth/me 不触发——否则未登录访客会被误弹登录页
    // （/up 与 App.vue 的 publicRoute 保持一致，9.9 复审 P2）
    const onPublicPage = location.pathname === '/login' || location.pathname === '/up'
      || location.pathname.startsWith('/s/')
    if (!onPublicPage) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search)
    }
    throw Object.assign(new Error('未登录'), { unauthorized: true })
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try { const b = await r.json(); detail = typeof b.detail === 'string' ? b.detail : detail } catch { /* */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}
const enc = (p: string) => p.split('/').filter(Boolean).map(encodeURIComponent).join('/')
const jpost = (url: string, body: unknown) =>
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
// 成员视角预览：只读 GET 附带 X-As-User（后端仅 admin 生效，其余请求一律原身份）
const getAs = (url: string) =>
  fetch(url, previewAs.value ? { headers: { 'X-As-User': previewAs.value } } : undefined)

export const api = {
  me: () => fetch('/api/auth/me').then(r => handle<Me>(r)),
  login: (username: string, password: string) => jpost('/api/login', { username, password }).then(r => handle<{ ok?: boolean; totp_required?: boolean; username?: string }>(r)),
  loginTotp: (username: string, password: string, code: string) => jpost('/api/login/totp', { username, password, code }).then(r => handle<{ ok: boolean }>(r)),
  totpSetup: () => jpost('/api/auth/totp/setup', {}).then(r => handle<{ secret: string; otpauth: string }>(r)),
  totpEnable: (code: string) => jpost('/api/auth/totp/enable', { code }).then(r => handle<{ ok: boolean; recovery_codes: string[] }>(r)),
  totpDisable: (old_password: string) => jpost('/api/auth/totp/disable', { old_password }).then(r => handle<{ ok: boolean }>(r)),
  totpRcRegen: (old_password: string) => jpost('/api/auth/totp/rc', { old_password }).then(r => handle<{ ok: boolean; recovery_codes: string[] }>(r)),
  notifications: () => fetch('/api/notifications').then(r => handle<{ unread: number; items: NotificationItem[] }>(r)),
  notificationsRead: () => jpost('/api/notifications/read', {}).then(r => handle<{ ok: boolean }>(r)),
  members: () => fetch('/api/members').then(r => handle<Member[]>(r)),
  albums: () => fetch('/api/albums').then(r => handle<Album[]>(r)),
  adminAddAlbum: (title: string, month: string) => jpost('/api/admin/albums', { title, month }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteAlbum: (id: string) => fetch(`/api/admin/albums/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  // 相册共创：成员可建精选专辑（admin/创建者可删）
  addAlbum: (title: string, month: string) => jpost('/api/albums', { title, month }).then(r => handle<{ ok: boolean; albums: Album[] }>(r)),
  deleteAlbum: (id: string) => fetch(`/api/albums/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  // 照片评论/表情回应已下线（改进建议 9.6-P2-13）
  // 数据导出（管理）：成员数据足迹 zip
  exportMember: (username: string) => jpost('/api/admin/export', { username }).then(r => handle<{ ok: boolean; url: string; name: string; files: number; bytes: number }>(r)),
  logout: () => fetch('/api/logout', { method: 'POST' }).then(() => undefined),
  changeUsername: (new_username: string, password: string) =>
    jpost('/api/auth/username', { new_username, password }).then(r => handle<{ ok: boolean; username: string }>(r)),
  changePassword: (old_password: string, new_password: string) =>
    jpost('/api/auth/password', { old_password, new_password }).then(r => handle<{ ok: boolean }>(r)),
  // 资料（v0.17）：昵称只是显示层，登录仍用用户名；头像按 uid 存服务端
  updateProfile: (nickname: string) =>
    fetch('/api/auth/profile', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nickname }) }).then(r => handle<{ ok: boolean; nickname: string }>(r)),
  uploadAvatar: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return fetch('/api/auth/avatar', { method: 'POST', body: fd }).then(r => handle<{ ok: boolean; avatar_path: string }>(r))
  },
  avatarUrl: (username: string, bust: number | string = 0) => `/api/auth/avatar/${encodeURIComponent(username)}?t=${bust}`,
  webdavPwdStatus: () => fetch('/api/auth/webdav-password').then(r => handle<{ set: boolean }>(r)),
  setWebdavPwd: (current_password: string, webdav_password: string) =>
    jpost('/api/auth/webdav-password', { current_password, webdav_password }).then(r => handle<{ ok: boolean; set: boolean }>(r)),
  adminSetWebdavPwd: (uid: number, value: string) =>
    jpost(`/api/admin/users/${uid}/webdav-password`, { value }).then(r => handle<{ ok: boolean; set: boolean }>(r)),

  // 手机 App 免密配对：生成一次性 6 位码（3 分钟有效）
  pairCode: () => jpost('/api/pair/code', {}).then(r => handle<{ code: string; expires_in: number }>(r)),

  // 登录设备管理（本账号）：App 配对出的设备在此可见/可吊销
  listDevices: () => fetch('/api/auth/devices').then(r => handle<{ devices: DeviceInfo[] }>(r)),
  revokeDevice: (id: number) => fetch(`/api/auth/devices/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),

  getWebhook: () => fetch('/api/admin/webhook').then(r => handle<{ url: string }>(r)),
  setWebhook: (url: string) => jpost('/api/admin/webhook', { url }).then(r => handle<{ ok: boolean; url: string }>(r)),
  testWebhook: () => jpost('/api/admin/webhook/test', {}).then(r => handle<{ ok: boolean }>(r)),
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
  // 全员端：单条已读/删除
  notificationRead: (id: number) => jpost(`/api/notifications/${id}/read`, {}).then(r => handle<{ ok: boolean }>(r)),
  notificationDelete: (id: number) =>
    fetch(`/api/notifications/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),

  // 每日健康日报（磁盘/备份/证书/隧道 → Webhook）
  getHealthReport: () => fetch('/api/admin/healthreport').then(r => handle<{ enabled: boolean; time: string; backup_dest: string; webhook_set: boolean }>(r)),
  setHealthReport: (enabled: boolean, time: string) => jpost('/api/admin/healthreport', { enabled, time }).then(r => handle<{ ok: boolean; enabled: boolean; time: string }>(r)),
  testHealthReport: () => jpost('/api/admin/healthreport/test', {}).then(r => handle<{ ok: boolean; sent: boolean | null; warn: boolean; report: string; webhook_set: boolean }>(r)),

  list: (path: string) => getAs(`/api/files/${enc(path)}`).then(r => handle<{ path: string; items: Entry[] }>(r)),
  mkdir: (path: string) => fetch(`/api/mkdir/${enc(path)}`, { method: 'POST' }).then(r => handle<{ ok: boolean }>(r)),
  rename: (path: string, new_name: string) => jpost('/api/rename', { path, new_name }).then(r => handle<{ ok: boolean }>(r)),
  remove: (path: string) => fetch(`/api/files/${enc(path)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  move: (sources: string[], dest_dir: string) => jpost('/api/move', { sources, dest_dir }).then(r => handle<{ results: { src: string; ok: boolean; error?: string; dest?: string }[] }>(r)),
  uploadDirect: (path: string, file: File) => {
    const f = new FormData(); f.append('file', file)
    return fetch(`/api/upload/${enc(path)}`, { method: 'POST', body: f }).then(r => handle<{ ok: boolean; deduped: boolean }>(r))
  },
  zip: (paths: string[]) => jpost('/api/zip', { paths }).then(r => handle<{ url: string; name: string }>(r)),

  trash: () => fetch('/api/trash').then(r => handle<TrashItem[]>(r)),
  trashRestore: (id: number) => fetch(`/api/trash/${id}/restore`, { method: 'POST' }).then(r => handle<{ ok: boolean; path: string }>(r)),
  trashDelete: (id: number) => fetch(`/api/trash/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  trashEmpty: () => fetch('/api/trash', { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),

  createShare: (path: string, expires_hours: number, password: string | null, max_downloads: number | null, target?: string | null) => {
    const f = new FormData()
    f.append('path', path); f.append('expires_hours', String(expires_hours))
    if (password) f.append('password', password)
    if (max_downloads) f.append('max_downloads', String(max_downloads))
    if (target) f.append('target', target)
    return fetch('/api/share', { method: 'POST', body: f }).then(r => handle<{ token: string; password?: string | null }>(r))
  },
  shares: () => getAs('/api/shares').then(r => handle<Record<string, ShareItem>>(r)).then(d => Object.entries(d).map(([token, s]) => ({ ...s, token }))),
  shareHistory: (token: string) => fetch(`/api/shares/${encodeURIComponent(token)}/history`).then(r => handle<ShareHistoryRow[]>(r)),
  revokeShare: (token: string) => fetch(`/api/shares/${token}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  shareQrUrl: (token: string) => `/api/share/${token}/qr`,
  shareLink: (token: string) => `${location.origin}/s/${token}`,

  disk: () => fetch('/api/disk').then(r => handle<DiskInfo>(r)),
  search: (q: string) => fetch(`/api/search?q=${encodeURIComponent(q)}`).then(r => handle<Entry[]>(r)),
  recent: (limit = 30) => fetch(`/api/recent?limit=${limit}`).then(r => handle<Entry[]>(r)),
  photos: (limit: number, offset: number, month?: string, uploader?: string, album?: string) => {
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (month) q.set('month', month)
    if (uploader) q.set('uploader', uploader)
    if (album) q.set('album', album)
    return getAs(`/api/photos?${q}`).then(r => handle<{ total: number; items: PhotoItem[] }>(r))
  },
  photoFacets: () => getAs('/api/photos/facets').then(r => handle<PhotoFacets>(r)),
  photoMeta: (path: string) => fetch(`/api/photo/meta?path=${encodeURIComponent(path)}`).then(r => handle<{ width: number | null; height: number | null; camera: string | null }>(r)),
  activity: (limit = 40) => fetch(`/api/activity?limit=${limit}`).then(r => handle<ActivityItem[]>(r)),

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
  adminCreateInvite: (role: string, hours: number) => jpost('/api/admin/invites', { role, hours }).then(r => handle<{ code: string }>(r)),
  adminInvites: () => fetch('/api/admin/invites').then(r => handle<{ code: string; role: string; expired: boolean; used: boolean }[]>(r)),
  adminDeleteInvite: (code: string) => fetch(`/api/admin/invites/${encodeURIComponent(code)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminGrants: (user_id?: number) => fetch(`/api/admin/grants${user_id ? `?user_id=${user_id}` : ''}`).then(r => handle<Grant[]>(r)),
  adminCreateGrant: (user_id: number, path: string, can_read: boolean, can_write: boolean, expires_hours?: number) =>
    jpost('/api/admin/grants', { user_id, path, can_read, can_write, expires_hours }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteGrant: (gid: number) => fetch(`/api/admin/grants/${gid}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  getPublicUrl: () => fetch('/api/public-url').then(r => handle<{ public_base_url: string; public_base_url_alt: string }>(r)),
  setPublicUrl: (url: string, urlAlt = '') => jpost('/api/admin/public-url', { url, url_alt: urlAlt }).then(r => handle<{ ok: boolean }>(r)),
}

export interface PublicShareApi {
  meta: (token: string) => Promise<ShareMeta>
  prepare: (token: string, password: string | null) => Promise<{ url: string; name: string }>
}
export const publicApi: PublicShareApi = {
  meta: (token) => fetch(`/api/share/meta/${encodeURIComponent(token)}`).then(r => handle<ShareMeta>(r)),
  prepare: (token, password) => jpost(`/api/share/${encodeURIComponent(token)}/prepare`, { password }).then(r => handle<{ url: string; name: string }>(r)),
}

export const CHUNK = 5 * 1024 * 1024

async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(d)).map(b => b.toString(16).padStart(2, '0')).join('')
}

/** 当前是否局域网访问（非私网地址即视为公网/FRP 隧道）。?net=public|lan 可强制指定（测试用）。 */
export function isLanAccess(): boolean {
  const q = new URLSearchParams(location.search).get('net')
  if (q === 'public') return false
  if (q === 'lan') return true
  const h = location.hostname
  if (!h || h === 'localhost' || h === '127.0.0.1' || h.endsWith('.local')) return true
  if (/^192\.168\.\d+\.\d+$/.test(h) || /^10\.\d+\.\d+\.\d+$/.test(h)) return true
  if (/^172\.(1[6-9]|2\d|3[01])\.\d+\.\d+$/.test(h)) return true
  if (h.startsWith('fe80:') || /^fd[0-9a-f]{2}:/i.test(h)) return true
  return false
}

function fmtDur(sec: number): string {
  if (!isFinite(sec) || sec < 0) return '--'
  sec = Math.round(sec)
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${String(sec % 60).padStart(2, '0')} 秒`
  return `${Math.floor(sec / 3600)} 小时 ${Math.round((sec % 3600) / 60)} 分`
}

const GB = 1024 ** 3
const PUBLIC_WARN_BYTES = 2 * GB          // 公网上传提醒阈值（局域网不提醒）

/** 公网上传大文件前弹窗提醒（2/5/10GB 三档措辞）；局域网或小文件直接放行。返回 false=用户取消 */
export async function confirmLargeUpload(file: File): Promise<boolean> {
  // ?warnmb= 覆盖提醒阈值（MB）——浏览器自动化/UI 验证用测试钩子，与 ?net= 配套
  const w = Number(new URLSearchParams(location.search).get('warnmb'))
  const threshold = w > 0 ? w * 1024 * 1024 : PUBLIC_WARN_BYTES
  if (isLanAccess() || file.size < threshold) return true
  const gb = (file.size / GB).toFixed(1)
  const est = fmtDur(file.size / (3.75 * 1024 * 1024)) // 按家庭上行 30Mbps ≈ 3.75MB/s 估算
  let tip: string
  if (file.size >= 10 * GB) {
    tip = `这是一个 ${gb} GB 的大文件，当前正在通过公网上传，预计需要 ${est} 甚至更久（家庭上行带宽通常远低于局域网）。中断后可断点续传，但强烈建议等回到局域网 Wi-Fi 再传。`
  } else if (file.size >= 5 * GB) {
    tip = `这是一个 ${gb} GB 的大文件，当前正在通过公网上传，预计需要 ${est} 左右（按上行 30Mbps 估算）。回到局域网内传输会快得多，确定现在继续吗？`
  } else {
    tip = `文件 ${gb} GB，当前正在通过公网上传，速度受家庭上行带宽限制（预计 ${est}）。可等回到局域网再传，也可现在继续。`
  }
  return confirmDlg(tip, { title: '🌐 公网上传大文件提醒', okText: '继续上传' })
}

/** XHR 发送单个分块（fetch 不支持上传进度；XHR upload.onprogress 可逐字节回报）。
 *  120s 超时：半开连接（断网/休眠唤醒）否则会永久卡死并发 worker */
function xhrSendChunk(url: string, blob: Blob, signal: AbortSignal | undefined,
                      onSent: (loaded: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.timeout = 120_000
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.upload.onprogress = e => { if (e.lengthComputable) onSent(e.loaded) }
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`HTTP ${xhr.status}`))
    xhr.onerror = () => reject(new Error('网络错误'))
    xhr.ontimeout = () => reject(new Error('超时'))
    xhr.onabort = () => reject(new DOMException('Aborted', 'AbortError'))
    xhr.send(blob)
  })
}

export interface UploadHooks {
  signal?: AbortSignal
  resumeSid?: string                       // 断点续传：复用既有会话（跨刷新）
  onSession?: (sid: string) => void        // 会话建立后回调（调用方持久化）
}

/** 分块上传：≤100MB 计算 sha256 走秒传；自适应分片（服务端下发）；≥256MB 并发 3 路；
 *  每块重试 3 次；显示速度/剩余时间；支持 resumeSid 断点续传（会话失效自动回落新会话） */
export async function uploadResumable(
  destDir: string, file: File,
  onProgress: (sent: number, total: number, stage: string) => void,
  hooks: UploadHooks = {},
): Promise<{ instant: boolean }> {
  const total = file.size
  const { signal } = hooks
  let sha: string | undefined
  if (!hooks.resumeSid && total <= 100 * 1024 * 1024) {
    signal?.throwIfAborted()
    onProgress(0, total, '计算指纹…')
    sha = await sha256Hex(await file.arrayBuffer())
  }
  signal?.throwIfAborted()

  let sid = hooks.resumeSid ?? ''
  let chunkSize = CHUNK
  let received = new Set<number>()
  if (sid) {
    // 续传：校验会话仍存活，拿已收块与分片大小
    try {
      const st = await fetch(`/api/upload/state/${sid}`)
        .then(r => handle<{ received: number[]; chunk_size: number }>(r))
      for (const i of st.received) received.add(i)
      chunkSize = st.chunk_size
    } catch {
      sid = ''          // 会话已过期/被清理 → 回落新会话
      received = new Set()
    }
  }
  if (!sid) {
    const init = await fetch('/api/upload/init', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dest_dir: destDir, filename: file.name, size: total, sha256: sha }),
    }).then(r => handle<{ instant: boolean; session?: string; chunk_size?: number }>(r))
    if (init.instant) { onProgress(total, total, '秒传'); return { instant: true } }
    sid = init.session!
    chunkSize = init.chunk_size ?? CHUNK
  }
  hooks.onSession?.(sid)

  const chunks = Math.ceil(total / chunkSize)

  let doneBytes = 0
  for (const i of received) doneBytes += Math.min(chunkSize, total - i * chunkSize)
  const inflight = new Map<number, number>()   // 进行中分块已发送字节（平滑进度）
  let nextIndex = 0
  let lastReport = 0
  const samples: { t: number; b: number }[] = []

  const report = (stage: string) => {
    const now = performance.now()
    if (now - lastReport < 400) return
    lastReport = now
    let sent = doneBytes
    for (const v of inflight.values()) sent += v
    sent = Math.min(sent, total)
    samples.push({ t: now, b: sent })
    while (samples.length > 2 && now - samples[0].t > 3000) samples.shift()
    let s = stage
    const dt = (now - samples[0].t) / 1000
    const db = sent - samples[0].b
    if (dt > 0.5 && db > 0) {
      const speed = db / dt
      s += ` · ${speed >= 1024 * 1024 ? (speed / 1024 / 1024).toFixed(1) + ' MB/s' : Math.round(speed / 1024) + ' KB/s'}`
      if (sent < total) s += ` · 剩余 ${fmtDur((total - sent) / speed)}`
    }
    onProgress(sent, total, s)
  }

  // 内部联动中止：任一 worker 失败即停其余（防僵尸 worker 后台空跑整个文件）；
  // 用户 signal 中止时同样联动
  const inner = new AbortController()
  const onOuterAbort = () => inner.abort()
  signal?.addEventListener('abort', onOuterAbort, { once: true })

  const sendChunk = async (i: number): Promise<void> => {
    const blob = file.slice(i * chunkSize, Math.min((i + 1) * chunkSize, total))
    for (let retry = 0; retry < 3; retry++) {
      try {
        inner.signal.throwIfAborted()
        await xhrSendChunk(`/api/upload/chunk/${sid}?index=${i}`, blob, inner.signal,
          loaded => { inflight.set(i, loaded); report(received.size ? '续传中' : '上传中') })
        doneBytes += blob.size
        inflight.delete(i)
        report(received.size ? '续传中' : '上传中')
        return
      } catch (e) {
        inflight.delete(i)
        if ((e as Error).name === 'AbortError') throw e
        if (retry === 2) { inner.abort(); throw new Error(`分块 ${i} 上传失败`) }
      }
    }
  }

  if (total >= 256 * 1024 * 1024 && chunks > 2) {
    // 大文件并发 3 路：减少高延迟链路空转
    const worker = async () => {
      while (true) {
        if (inner.signal.aborted) return
        const i = nextIndex++
        if (i >= chunks) return
        if (received.has(i)) continue
        await sendChunk(i)
      }
    }
    try {
      await Promise.all(Array.from({ length: 3 }, () => worker()))
    } finally {
      signal?.removeEventListener('abort', onOuterAbort)
    }
  } else {
    try {
      for (let i = 0; i < chunks; i++) {
        if (received.has(i)) continue
        await sendChunk(i)
      }
    } finally {
      signal?.removeEventListener('abort', onOuterAbort)
    }
  }

  signal?.throwIfAborted()
  onProgress(total, total, '合并校验…')
  const fin = await fetch(`/api/upload/finish/${sid}`, { method: 'POST', signal })
    .then(r => handle<{ ok: boolean; deduped: boolean }>(r))
  return { instant: fin.deduped }
}
