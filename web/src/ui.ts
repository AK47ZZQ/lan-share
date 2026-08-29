// 轻量 toast + 确认对话框（模块级响应式状态）
import { reactive } from 'vue'

export const toasts = reactive<{ id: number; text: string; kind: 'ok' | 'err' | 'info' }[]>([])
let seq = 0
export function toast(text: string, kind: 'ok' | 'err' | 'info' = 'info') {
  const id = ++seq
  toasts.push({ id, text, kind })
  setTimeout(() => { const i = toasts.findIndex(t => t.id === id); if (i >= 0) toasts.splice(i, 1) }, 3200)
}

export function fmtTs(ts: number | string): string {
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (isNaN(d.getTime())) return '-'
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export const ACTION_NAMES: Record<string, string> = {
  upload: '上传了', upload_instant: '秒传了', mkdir: '新建了目录', share: '创建了分享',
  share_download: '有人下载了分享', invite_redeemed: '加入了家庭空间',
  trash: '删除了', restore: '恢复了', move: '移动了', rename: '重命名了',
}

export function human(size: number | null | undefined): string {
  if (size == null) return '-'
  let s = size
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (s < 1024) return `${s.toFixed(s < 10 && unit !== 'B' ? 1 : 0)} ${unit}`
    s /= 1024
  }
  return `${s.toFixed(1)} PB`
}
