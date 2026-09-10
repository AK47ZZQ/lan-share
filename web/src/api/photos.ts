// 照片与相册域（v3.48 从 api.ts 按域拆分）：时间线/facets/元数据/精选专辑。
import { getAs, handle, jpost } from './core'
import type { Album, PhotoFacets, PhotoItem } from './types'

export const photosApi = {
  photos: (limit: number, offset: number, month?: string, uploader?: string, album?: string) => {
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (month) q.set('month', month)
    if (uploader) q.set('uploader', uploader)
    if (album) q.set('album', album)
    return getAs(`/api/photos?${q}`).then(r => handle<{ total: number; items: PhotoItem[] }>(r))
  },
  photoFacets: () => getAs('/api/photos/facets').then(r => handle<PhotoFacets>(r)),
  photoMeta: (path: string) => fetch(`/api/photo/meta?path=${encodeURIComponent(path)}`).then(r => handle<{ width: number | null; height: number | null; camera: string | null }>(r)),
  albums: () => fetch('/api/albums').then(r => handle<Album[]>(r)),
  // 相册共创：成员可建精选专辑（admin/创建者可删）
  addAlbum: (title: string, month: string) => jpost('/api/albums', { title, month }).then(r => handle<{ ok: boolean; albums: Album[] }>(r)),
  deleteAlbum: (id: string) => fetch(`/api/albums/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  // 照片评论/表情回应已下线（改进建议 9.6-P2-13）
}
