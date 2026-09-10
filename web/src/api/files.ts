// 文件域（v3.48 从 api.ts 按域拆分）：列表/目录操作/直传/打包/回收站/搜索/磁盘。
import { enc, handle, jpost } from './core'
import type { DiskInfo, Entry, TrashItem } from './types'

export const filesApi = {
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

  disk: () => fetch('/api/disk').then(r => handle<DiskInfo>(r)),
  search: (q: string) => fetch(`/api/search?q=${encodeURIComponent(q)}`).then(r => handle<Entry[]>(r)),
  recent: (limit = 30) => fetch(`/api/recent?limit=${limit}`).then(r => handle<Entry[]>(r)),
}
