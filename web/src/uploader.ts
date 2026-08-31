// 上传队列共享引擎（HomeView / UpView 共用）：状态、取消、重试、覆盖提醒
import { computed, reactive } from 'vue'
import { confirmLargeUpload, uploadResumable } from './api'
import { confirmDlg } from './ui'

export interface UplItem {
  id: number
  name: string
  sent: number
  total: number
  stage: string
  status: 'queue' | 'up' | 'done' | 'fail' | 'cancel'
  file: File
  ctrl: AbortController | null
}

let seq = 0
let batchAborted = false

export const upl = reactive({
  items: [] as UplItem[],
  collapsed: localStorage.getItem('uplCollapsed') === '1',
})

export function toggleCollapsed() {
  upl.collapsed = !upl.collapsed
  localStorage.setItem('uplCollapsed', upl.collapsed ? '1' : '0')
}

export const uploadingNow = computed(() =>
  upl.items.some(i => i.status === 'up' || i.status === 'queue'))

export const hasFinished = computed(() =>
  upl.items.some(i => i.status === 'done' || i.status === 'fail' || i.status === 'cancel'))

export const uplSummary = computed(() => {
  const items = upl.items
  const total = items.reduce((a, i) => a + i.total, 0)
  const sent = items.reduce((a, i) => a + (i.status === 'done' || i.status === 'cancel' ? (i.status === 'done' ? i.total : i.sent) : i.sent), 0)
  return {
    count: items.length,
    pct: total ? Math.round(Math.min(sent, total) / total * 100) : 0,
    active: items.filter(i => i.status === 'up' || i.status === 'queue').length,
    done: items.filter(i => i.status === 'done').length,
    failed: items.filter(i => i.status === 'fail').length,
  }
})

export interface UploadOpts {
  destDir: string
  existsNames?: string[]       // 当前目录已有文件名 → 同名覆盖提醒
  screenshotRename?: boolean   // 剪贴板 image.png → 截图-时间戳.png
  onEachDone?: (name: string) => void
  onEachFail?: (name: string) => void
}

export async function uploadFiles(files: File[], opts: UploadOpts): Promise<void> {
  batchAborted = false
  // 同名覆盖提醒（一次性列出；取消则跳过这些文件）
  if (opts.existsNames?.length) {
    const have = new Set(opts.existsNames)
    const clashes = files.filter(f => have.has(f.name))
    if (clashes.length) {
      const ok = await confirmDlg(
        `以下 ${clashes.length} 个文件已存在，继续上传将覆盖（旧版本自动移入回收站）：\n${clashes.map(f => f.name).join('、')}`,
        { title: '同名文件覆盖确认', okText: '覆盖上传' })
      if (!ok) files = files.filter(f => !have.has(f.name))
      if (!files.length) return
    }
  }
  // 先整批入队（弹窗确认在前），再顺序上传——队列立即可见、可取消
  const batch: UplItem[] = []
  for (let f of files) {
    if (opts.screenshotRename && /^image(\.\w+)?$/i.test(f.name) && f.type.startsWith('image/')) {
      const d = new Date()
      const p = (n: number) => String(n).padStart(2, '0')
      const st = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
      f = new File([f], `截图-${st}${f.name.match(/(\.\w+)$/)?.[1] ?? '.png'}`, { type: f.type })
    }
    if (!(await confirmLargeUpload(f))) continue  // 公网大文件：取消则跳过
    batch.push({
      id: ++seq, name: f.name, sent: 0, total: f.size, stage: '排队',
      status: 'queue', file: f, ctrl: null,
    })
  }
  upl.items.push(...batch)
  for (const item of batch) {
    if (batchAborted) break
    await runItem(opts, item)
  }
}

async function runItem(opts: UploadOpts, item: UplItem) {
  if (item.status === 'cancel') return  // 排队时已被取消
  item.status = 'up'; item.stage = '准备…'; item.sent = 0
  item.ctrl = new AbortController()
  try {
    const r = await uploadResumable(opts.destDir, item.file, (sent, total, stage) => {
      item.sent = sent; item.total = total; item.stage = stage
    }, item.ctrl.signal)
    item.status = 'done'; item.stage = r.instant ? '秒传完成 ✓' : '完成 ✓'
    opts.onEachDone?.(item.name)
    setTimeout(() => {
      const k = upl.items.indexOf(item)
      if (k >= 0 && upl.items[k].status === 'done') upl.items.splice(k, 1)
    }, 2500)
  } catch (e) {
    if ((e as Error).name === 'AbortError') {
      item.status = 'cancel'; item.stage = '已取消'
    } else {
      item.status = 'fail'; item.stage = '失败：' + (e as Error).message
      opts.onEachFail?.(item.name)
    }
  }
}

export function retryItem(opts: UploadOpts, item: UplItem) {
  if (item.status !== 'fail' && item.status !== 'cancel') return
  item.status = 'queue'  // 绕过 runItem 的"排队时已取消"守卫
  return runItem(opts, item)
}

export function cancelItem(item: UplItem) {
  if (item.status === 'queue') { item.status = 'cancel'; item.stage = '已取消'; return }
  item.ctrl?.abort()
}

export function cancelAll() {
  batchAborted = true
  for (const i of upl.items) {
    if (i.status === 'queue') { i.status = 'cancel'; i.stage = '已取消' }
    else if (i.status === 'up') i.ctrl?.abort()
  }
}

export function clearFinished() {
  upl.items = upl.items.filter(i => i.status === 'up' || i.status === 'queue')
}

// 上传中关闭/刷新页面拦截（大文件保护；断点续传可减轻但不打断更好）
window.addEventListener('beforeunload', (e) => {
  if (uploadingNow.value) { e.preventDefault(); e.returnValue = '' }
})
