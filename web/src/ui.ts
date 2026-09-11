// 轻量 toast + 确认对话框（模块级响应式状态）
// 纯函数（fmtTs/human/iconForName/fmtSpeed/ACTION_NAMES）已迁往 ./pure
// （vue-free，node --test 可测）；这里 re-export 保持既有 import 路径不变。
import { reactive } from 'vue'

export const toasts = reactive<{ id: number; text: string; kind: 'ok' | 'err' | 'info' }[]>([])
let seq = 0
export function toast(text: string, kind: 'ok' | 'err' | 'info' = 'info') {
  const id = ++seq
  toasts.push({ id, text, kind })
  // 错误类 6s（体验优化）：长报错 3.2s 读不完；成功/提示保持 3.2s
  const ms = kind === 'err' ? 6000 : 3200
  setTimeout(() => { const i = toasts.findIndex(t => t.id === id); if (i >= 0) toasts.splice(i, 1) }, ms)
}

export { fmtTs, ACTION_NAMES, human, iconForName, fmtSpeed } from './pure'

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
