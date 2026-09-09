// 轻量 toast + 确认对话框（模块级响应式状态）
import { reactive } from 'vue'
import { AUD_EXTS, IMG_EXTS, VID_EXTS, extOfName } from './filekinds'

export const toasts = reactive<{ id: number; text: string; kind: 'ok' | 'err' | 'info' }[]>([])
let seq = 0
export function toast(text: string, kind: 'ok' | 'err' | 'info' = 'info') {
  const id = ++seq
  toasts.push({ id, text, kind })
  // 错误类 6s（体验优化）：长报错 3.2s 读不完；成功/提示保持 3.2s
  const ms = kind === 'err' ? 6000 : 3200
  setTimeout(() => { const i = toasts.findIndex(t => t.id === id); if (i >= 0) toasts.splice(i, 1) }, ms)
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

// 媒体分类取自 filekinds 唯一事实源（9.9 复审 P3：两处手写列表已漂移）；
// pdf/压缩包/文档是图标专属分类，预览路由不用，留在这里
const NO_DOT = (a: string[]) => a.map(e => e.slice(1))
const VID_ICON = NO_DOT(VID_EXTS), AUD_ICON = NO_DOT(AUD_EXTS), IMG_ICON = NO_DOT(IMG_EXTS)

export function iconForName(name: string): string {
  const ext = extOfName(name).slice(1)
  if (VID_ICON.includes(ext)) return '🎬'
  if (AUD_ICON.includes(ext)) return '🎵'
  if (['zip', '7z', 'rar'].includes(ext)) return '🗜️'
  if (ext === 'pdf') return '📕'
  if (['doc', 'docx'].includes(ext)) return '📘'
  if (['xls', 'xlsx'].includes(ext)) return '📗'
  if (IMG_ICON.includes(ext)) return '🖼️'
  return '📄'
}

export function fmtSpeed(bps: number): string {
  if (!isFinite(bps) || bps <= 0) return '--'
  return bps >= 1024 * 1024 ? `${(bps / 1024 / 1024).toFixed(1)} MB/s` : `${Math.round(bps / 1024)} KB/s`
}

// ── Vue 版确认/输入对话框（替代浏览器原生 confirm/prompt）──
export const dlg = reactive({
  open: false,
  kind: 'confirm' as 'confirm' | 'prompt',
  title: '', text: '', value: '', placeholder: '',
  danger: false,
  okText: '',
  _resolve: null as null | ((v: unknown) => void),
})
export function confirmDlg(text: string, opts?: { title?: string; danger?: boolean; okText?: string }): Promise<boolean> {
  return new Promise(resolve => {
    Object.assign(dlg, { open: true, kind: 'confirm', title: opts?.title ?? '请确认', text,
      value: '', danger: !!opts?.danger, okText: opts?.okText ?? '',
      _resolve: resolve as (v: unknown) => void })
  })
}
export function promptDlg(text: string, value = '', opts?: { title?: string; placeholder?: string }): Promise<string | null> {
  return new Promise(resolve => {
    Object.assign(dlg, { open: true, kind: 'prompt', title: opts?.title ?? '请输入', text,
      value, placeholder: opts?.placeholder ?? '', danger: false, okText: '',
      _resolve: resolve as (v: unknown) => void })
  })
}
export function dlgOk() {
  dlg.open = false
  dlg._resolve?.(dlg.kind === 'prompt' ? dlg.value : true)
  dlg._resolve = null
}
export function dlgCancel() {
  dlg.open = false
  dlg._resolve?.(dlg.kind === 'prompt' ? null : false)
  dlg._resolve = null
}
