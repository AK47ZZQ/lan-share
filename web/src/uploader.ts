// 上传队列共享引擎（HomeView / UpView 共用）：
// 状态队列、取消、暂停/恢复、跨刷新断点续传（localStorage 存会话号）、
// 速度采样（sparkline 用）、系统级完成通知
import { computed, reactive, ref } from 'vue'
import { confirmLargeUpload, uploadResumable } from './api'
import { confirmDlg } from './ui'

export interface UplItem {
  id: number
  name: string
  sent: number
  total: number
  stage: string
  status: 'queue' | 'up' | 'paused' | 'done' | 'fail' | 'cancel'
  file: File
  ctrl: AbortController | null
  destDir: string
  key: string              // 断点续传键：destDir|name|size|lastModified
  uopts: Pick<UploadOpts, 'onEachDone' | 'onEachFail'>   // 回调随 item 走（全局泵防批次串扰）
}

let seq = 0
let carryBytes = 0        // 已完成文件字节数（速度采样分母连续性）

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
  const sent = items.reduce((a, i) => a + (i.status === 'done' ? i.total : i.sent), 0)
  return {
    count: items.length,
    pct: total ? Math.round(Math.min(sent, total) / total * 100) : 0,
    active: items.filter(i => i.status === 'up' || i.status === 'queue').length,
    paused: items.filter(i => i.status === 'paused').length,
    done: items.filter(i => i.status === 'done').length,
    failed: items.filter(i => i.status === 'fail').length,
  }
})

// ── 速度采样（sparkline）：每 500ms 聚合一次，滚动 60s 窗口 ──
export const uplSpeed = reactive({ samples: [] as number[] })
let ticker: ReturnType<typeof setInterval> | null = null
let lastAgg = -1
let lastT = 0

function ensureTicker() {
  if (ticker !== null) return
  ticker = setInterval(() => {
    if (!uploadingNow.value) {
      clearInterval(ticker!); ticker = null; lastAgg = -1
      uplSpeed.samples.splice(0)          // 清残影：下次上传从头画
      return
    }
    const agg = carryBytes + upl.items.reduce((a, i) => a + (i.status === 'up' ? i.sent : 0), 0)
    const now = performance.now()
    if (lastAgg >= 0 && now > lastT) {
      const sp = Math.max(0, (agg - lastAgg) / ((now - lastT) / 1000))
      uplSpeed.samples.push(sp)
      if (uplSpeed.samples.length > 120) uplSpeed.samples.shift()
    }
    lastAgg = agg; lastT = now
  }, 500)
}

// ── 断点续传：会话号持久化（key → sid），TTL 与服务端一致 3 天 ──
const UPSID_KEY = 'lsUpsid'
const UPSID_TTL = 3 * 86400 * 1000
interface SidEntry { sid: string; name: string; size: number; ts: number }

function loadSidMap(): Record<string, SidEntry> {
  try {
    const m = JSON.parse(localStorage.getItem(UPSID_KEY) || '{}') as Record<string, SidEntry>
    const now = Date.now()
    let dirty = false
    for (const k of Object.keys(m)) {
      if (now - m[k].ts > UPSID_TTL) { delete m[k]; dirty = true }
    }
    if (dirty) localStorage.setItem(UPSID_KEY, JSON.stringify(m))
    return m
  } catch { return {} }
}
function saveSidMap(m: Record<string, SidEntry>) {
  try { localStorage.setItem(UPSID_KEY, JSON.stringify(m)) } catch { /* */ }
  syncResumables()
}
function upKey(destDir: string, f: File): string {
  return `${destDir}|${f.name}|${f.size}|${f.lastModified}`
}

// 未完成会话提示（刷新后队列空时展示"重新选择同一文件可续传"）
export const resumables = ref<SidEntry[]>([])
function syncResumables() {
  resumables.value = Object.entries(loadSidMap()).map(([, v]) => v).sort((a, b) => a.ts - b.ts)
}
syncResumables()
export function clearResumables() {
  localStorage.removeItem(UPSID_KEY)
  syncResumables()
}

// ── 系统级完成通知 ──
function ensureNotifPerm() {
  try {
    if ('Notification' in window && Notification.permission === 'default') {
      void Notification.requestPermission()
    }
  } catch { /* */ }
}
function notifyDone(startedAt: number, done: number, failed: number) {
  try {
    if (!('Notification' in window) || Notification.permission !== 'granted') return
    if (!document.hidden && Date.now() - startedAt < 15000) return   // 前台且很快完成就不打扰
    const body = failed ? `${done} 个完成，${failed} 个失败` : `${done} 个文件上传完成`
    new Notification('LAN Share', { body, icon: '/static/favicon.svg' })
  } catch { /* */ }
}

export interface UploadOpts {
  destDir: string
  existsNames?: string[]       // 当前目录已有文件名 → 同名覆盖提醒
  screenshotRename?: boolean   // 剪贴板 image.png → 截图-时间戳.png
  onEachDone?: (name: string) => void
  onEachFail?: (name: string) => void
}
type ItemCallbacks = Pick<UploadOpts, 'onEachDone' | 'onEachFail'>

export async function uploadFiles(files: File[], opts: UploadOpts): Promise<void> {
  ensureNotifPerm()
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
  // 先整批入队（弹窗确认在前），再顺序上传——队列立即可见、可取消/暂停
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
      destDir: opts.destDir, key: upKey(opts.destDir, f),
      uopts: { onEachDone: opts.onEachDone, onEachFail: opts.onEachFail },
    })
  }
  upl.items.push(...batch)
  ensureTicker()
  const startedAt = Date.now()
  await pumpAll()
  const done = batch.filter(i => i.status === 'done').length
  const failed = batch.filter(i => i.status === 'fail').length
  if (batch.length) notifyDone(startedAt, done, failed)
}

// 队列泵：顺序跑所有排队项；暂停/失败即停，恢复/重试时重新驱动
let pumping = false
async function pumpAll() {
  if (pumping) return
  pumping = true
  try {
    while (true) {
      const next = upl.items.find(i => i.status === 'queue')
      if (!next) break
      await runItem(next)
      if (next.status === 'paused') break
    }
  } finally { pumping = false }
}

async function runItem(item: UplItem) {
  if (item.status === 'cancel') return  // 排队时已被取消
  item.status = 'up'; item.stage = '准备…'; item.sent = 0
  item.ctrl = new AbortController()
  const sidMap = loadSidMap()
  const resumeSid = sidMap[item.key]?.sid
  try {
    const r = await uploadResumable(item.destDir, item.file, (sent, total, stage) => {
      item.sent = sent; item.total = total; item.stage = stage
    }, {
      signal: item.ctrl.signal,
      resumeSid,
      onSession: sid => {
        const m = loadSidMap()
        m[item.key] = { sid, name: item.name, size: item.total, ts: Date.now() }
        saveSidMap(m)
      },
    })
    item.status = 'done'; item.stage = r.instant ? '秒传完成 ✓' : '完成 ✓'
    carryBytes += item.total
    const m = loadSidMap()
    if (m[item.key]) { delete m[item.key]; saveSidMap(m) }   // 传完清持久化会话
    item.uopts.onEachDone?.(item.name)
    setTimeout(() => {
      const k = upl.items.indexOf(item)
      if (k >= 0 && upl.items[k].status === 'done') upl.items.splice(k, 1)
    }, 2500)
  } catch (e) {
    if ((e as Error).name === 'AbortError') {
      // status 由 pauseItem/cancelItem 在 abort 前写入，TS 窄化看不到跨模块赋值
      const stNow = item.status as UplItem['status']
      if (stNow === 'paused') {
        item.stage = '已暂停 · 可续传'
      } else {
        item.status = 'cancel'; item.stage = '已取消'
        const m = loadSidMap()
        if (m[item.key]) { delete m[item.key]; saveSidMap(m) }
      }
    } else {
      item.status = 'fail'; item.stage = '失败：' + (e as Error).message
      item.uopts.onEachFail?.(item.name)
    }
  }
}

export function retryItem(item: UplItem) {
  if (item.status !== 'fail' && item.status !== 'cancel') return
  item.status = 'queue'  // 绕过 runItem 的"排队时已取消"守卫
  ensureTicker()
  return pumpAll()
}

export function resumeItem(item: UplItem) {
  if (item.status !== 'paused') return
  item.status = 'queue'
  ensureTicker()
  return pumpAll()
}

function dropSidEntry(item: UplItem) {
  const m = loadSidMap()
  if (m[item.key]) { delete m[item.key]; saveSidMap(m) }
}

export function pauseItem(item: UplItem) {
  if (item.status === 'queue') { item.status = 'paused'; item.stage = '已暂停 · 可续传'; return }
  if (item.status !== 'up') return
  item.status = 'paused'   // 先置状态，runItem 的 AbortError 分支据此保留会话
  item.ctrl?.abort()
}

export function cancelItem(item: UplItem) {
  if (item.status === 'queue') { item.status = 'cancel'; item.stage = '已取消'; return }
  const wasPaused = item.status === 'paused'
  item.status = 'cancel'
  item.ctrl?.abort()
  if (wasPaused) dropSidEntry(item)   // 暂停项被取消：清持久化会话（runItem 已返回，无人再清）
}

export function cancelAll() {
  for (const i of upl.items) {
    if (i.status === 'queue') { i.status = 'cancel'; i.stage = '已取消' }
    else if (i.status === 'up' || i.status === 'paused') cancelItem(i)
  }
}

export function clearFinished() {
  for (const i of upl.items) {
    if (i.status === 'paused') dropSidEntry(i)   // 被丢弃的暂停项不留续传记录
  }
  upl.items = upl.items.filter(i => i.status === 'up' || i.status === 'queue' || i.status === 'paused')
}

// 上传中关闭/刷新页面拦截（大文件保护；已传分块可跨刷新续传）
window.addEventListener('beforeunload', (e) => {
  if (uploadingNow.value) { e.preventDefault(); e.returnValue = '' }
})
