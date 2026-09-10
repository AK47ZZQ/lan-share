// 分享域（v3.48 从 api.ts 按域拆分）：创建/列表/取件记录/撤销/二维码 + 公开取件 API。
import { getAs, handle, jpost } from './core'
import type { ShareHistoryRow, ShareItem, ShareMeta } from './types'

export const sharesApi = {
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
}

export interface PublicShareApi {
  meta: (token: string) => Promise<ShareMeta>
  prepare: (token: string, password: string | null) => Promise<{ url: string; name: string }>
}
export const publicApi: PublicShareApi = {
  meta: (token) => fetch(`/api/share/meta/${encodeURIComponent(token)}`).then(r => handle<ShareMeta>(r)),
  prepare: (token, password) => jpost(`/api/share/${encodeURIComponent(token)}/prepare`, { password }).then(r => handle<{ url: string; name: string }>(r)),
}
