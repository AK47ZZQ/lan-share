// 全量 API 客户端（cookie 会话）

export interface Entry {
  name: string; path: string; is_dir: boolean
  size: number | null; size_human: string; modified: string; ext: string | null
}
export interface Me { username: string; role: 'admin' | 'member' | 'guest'; quota_bytes: number | null }
export interface TrashItem { id: number; name: string; orig_path: string; size_human: string; deleted_at: string; days_left: number }
export interface ShareItem { token: string; name: string; path: string; size_human: string; expires_at: number; downloads: number; max_downloads: number | null }
export interface ShareMeta { name: string; size_human: string; expires_in: string; needs_password: boolean; max_downloads: number | null; downloads: number }
export interface AdminUser { id: number; username: string; role: string; is_active: boolean; quota_bytes: number | null; quota_human: string; created_at: string }
export interface Grant { id: number; user_id: number; path: string; can_read: boolean; can_write: boolean; expires_at: number | null }
export interface ActivityItem { ts: number; username: string; action: string; target: string; detail: string }
export interface DiskInfo { total: string; used: string; free: string; free_bytes: number; share_size: string; warning: boolean }

async function handle<T>(r: Response): Promise<T> {
  if (r.status === 401) {
    // 会话过期：整页跳登录（带 next 回跳），避免停留在失效页面
    if (!location.pathname.startsWith('/login')) {
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

export const api = {
  me: () => fetch('/api/auth/me').then(r => handle<Me>(r)),
  login: (username: string, password: string) => jpost('/api/login', { username, password }).then(r => handle<{ ok: boolean }>(r)),
  logout: () => fetch('/api/logout', { method: 'POST' }).then(() => undefined),

  list: (path: string) => fetch(`/api/files/${enc(path)}`).then(r => handle<{ path: string; items: Entry[] }>(r)),
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

  createShare: (path: string, expires_hours: number, password: string | null, max_downloads: number | null) => {
    const f = new FormData()
    f.append('path', path); f.append('expires_hours', String(expires_hours))
    if (password) f.append('password', password)
    if (max_downloads) f.append('max_downloads', String(max_downloads))
    return fetch('/api/share', { method: 'POST', body: f }).then(r => handle<{ token: string }>(r))
  },
  shares: () => fetch('/api/shares').then(r => handle<ShareItem[]>(r)),
  revokeShare: (token: string) => fetch(`/api/shares/${token}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  shareQrUrl: (token: string) => `/api/share/${token}/qr`,
  shareLink: (token: string) => `${location.origin}/s/${token}`,

  disk: () => fetch('/api/disk').then(r => handle<DiskInfo>(r)),
  search: (q: string) => fetch(`/api/search?q=${encodeURIComponent(q)}`).then(r => handle<Entry[]>(r)),
  recent: (limit = 30) => fetch(`/api/recent?limit=${limit}`).then(r => handle<Entry[]>(r)),
  photos: (limit: number, offset: number) =>
    fetch(`/api/photos?limit=${limit}&offset=${offset}`).then(r => handle<{ total: number; items: { path: string; name: string; taken: string; thumb: string }[] }>(r)),
  activity: (limit = 40) => fetch(`/api/activity?limit=${limit}`).then(r => handle<ActivityItem[]>(r)),

  adminUsers: () => fetch('/api/admin/users').then(r => handle<AdminUser[]>(r)),
  adminPatchUser: (uid: number, body: { is_active?: boolean; quota_bytes?: number | null; role?: string }) =>
    fetch(`/api/admin/users/${uid}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteUser: (uid: number) => fetch(`/api/admin/users/${uid}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminCreateInvite: (role: string, hours: number) => jpost('/api/admin/invites', { role, hours }).then(r => handle<{ code: string }>(r)),
  adminInvites: () => fetch('/api/admin/invites').then(r => handle<{ code: string; role: string; expired: boolean; used: boolean }[]>(r)),
  adminDeleteInvite: (code: string) => fetch(`/api/admin/invites/${encodeURIComponent(code)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  adminGrants: (user_id?: number) => fetch(`/api/admin/grants${user_id ? `?user_id=${user_id}` : ''}`).then(r => handle<Grant[]>(r)),
  adminCreateGrant: (user_id: number, path: string, can_read: boolean, can_write: boolean, expires_hours?: number) =>
    jpost('/api/admin/grants', { user_id, path, can_read, can_write, expires_hours }).then(r => handle<{ ok: boolean }>(r)),
  adminDeleteGrant: (gid: number) => fetch(`/api/admin/grants/${gid}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
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

/** 分块上传（≤100MB 计算 sha256 走秒传；每块重试 3 次） */
export async function uploadResumable(
  destDir: string, file: File,
  onProgress: (sent: number, total: number, stage: string) => void,
): Promise<{ instant: boolean }> {
  const total = file.size
  let sha: string | undefined
  if (total <= 100 * 1024 * 1024) {
    onProgress(0, total, '计算指纹…')
    sha = await sha256Hex(await file.arrayBuffer())
  }
  const init = await fetch('/api/upload/init', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dest_dir: destDir, filename: file.name, size: total, sha256: sha }),
  }).then(r => handle<{ instant: boolean; session?: string; chunk_size?: number }>(r))
  if (init.instant) { onProgress(total, total, '秒传'); return { instant: true } }

  const sid = init.session!
  const chunkSize = init.chunk_size ?? CHUNK
  const chunks = Math.ceil(total / chunkSize)
  // 断点续传：查询已收块
  let received = new Set<number>()
  try {
    const st = await fetch(`/api/upload/state/${sid}`).then(r => handle<{ received: number[] }>(r))
    received = new Set(st.received)
  } catch { /* */ }
  for (let i = 0; i < chunks; i++) {
    if (received.has(i)) { onProgress(Math.min((i + 1) * chunkSize, total), total, '续传'); continue }
    const blob = file.slice(i * chunkSize, Math.min((i + 1) * chunkSize, total))
    let ok = false
    for (let retry = 0; retry < 3 && !ok; retry++) {
      try {
        const r = await fetch(`/api/upload/chunk/${sid}?index=${i}`, { method: 'POST', body: blob })
        if (!r.ok) throw new Error(`chunk ${i}`)
        ok = true
      } catch { if (retry === 2) throw new Error(`分块 ${i} 上传失败`) }
    }
    onProgress(Math.min((i + 1) * chunkSize, total), total, '上传中')
  }
  const fin = await fetch(`/api/upload/finish/${sid}`, { method: 'POST' })
    .then(r => handle<{ ok: boolean; deduped: boolean }>(r))
  return { instant: fin.deduped }
}
