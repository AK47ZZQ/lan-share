// 极简 API 层：cookie 会话 + JSON 归一错误

export interface Entry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  size_human: string
  modified: string
  ext: string | null
}

export interface Me {
  username: string
  role: 'admin' | 'member' | 'guest'
  quota_bytes: number | null
}

async function handle<T>(r: Response): Promise<T> {
  if (r.status === 401) {
    throw Object.assign(new Error('未登录'), { unauthorized: true })
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const body = await r.json()
      detail = typeof body.detail === 'string' ? body.detail : detail
    } catch { /* keep default */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export const api = {
  me: () => fetch('/api/auth/me').then(r => handle<Me>(r)),
  login: (username: string, password: string) =>
    fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then(r => handle<{ ok: boolean }>(r)),
  logout: () => fetch('/api/logout', { method: 'POST' }).then(() => undefined),
  list: (path: string) =>
    fetch(`/api/files/${encodePath(path)}`).then(r => handle<{ path: string; items: Entry[] }>(r)),
  upload: (path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`/api/upload/${encodePath(path)}`, { method: 'POST', body: form })
      .then(r => handle<{ ok: boolean; deduped: boolean }>(r))
  },
  mkdir: (path: string) =>
    fetch(`/api/mkdir/${encodePath(path)}`, { method: 'POST' }).then(r => handle<{ ok: boolean }>(r)),
}

function encodePath(path: string): string {
  return path.split('/').filter(Boolean).map(encodeURIComponent).join('/')
}
