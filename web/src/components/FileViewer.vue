<script setup lang="ts">
// 统一文件预览器：文件页/照片页共用。沉浸外壳 + 相邻导航 + 键盘 + 触屏手势 +
// 图片缩放平移/中图档、文本/Markdown/代码/CSV 渲染、docx、音频连播、视频倍速/画中画。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, type Me } from '../api'
import { confirmDlg, human, promptDlg, toast } from '../ui'
import Modal from './Modal.vue'
import ShareModal from './ShareModal.vue'
import 'highlight.js/styles/github-dark.css'

// 预览库按需加载（避免拖累首屏 bundle；首次打开文本/docx 预览时才拉 chunk）
let _hljs: typeof import('highlight.js/lib/common')['default'] | null = null
let _marked: typeof import('marked')['marked'] | null = null
let _purify: typeof import('dompurify')['default'] | null = null
async function textLibs() {
  if (!_hljs || !_marked || !_purify) {
    const [h, m, d] = await Promise.all([
      import('highlight.js/lib/common'),
      import('marked'),
      import('dompurify'),
    ])
    _hljs = h.default; _marked = m.marked; _purify = d.default
  }
  return { hljs: _hljs!, marked: _marked!, purify: _purify! }
}

export interface ViewerItem {
  path: string
  name: string
  ext?: string | null
  size?: number | null
  size_human?: string
  modified?: string
  taken?: string
  uploader?: string
  is_dir?: boolean
}

const props = defineProps<{ items: ViewerItem[]; modelValue: number; me: Me }>()
const emit = defineEmits<{
  'update:modelValue': [v: number]
  close: []
  share: [item: ViewerItem]
  refresh: []
}>()

const enc = (p: string) => p.split('/').filter(Boolean).map(encodeURIComponent).join('/')

const item = computed(() => props.items[props.modelValue])
const IMG = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg']
const VID = ['.mp4', '.mkv', '.mov', '.webm', '.avi']
const AUD = ['.mp3', '.flac', '.wav', '.m4a', '.ogg']
const TEXT = ['.txt', '.md', '.log', '.json', '.csv', '.py', '.js', '.ts', '.vue', '.css',
  '.html', '.xml', '.yml', '.yaml', '.ini', '.bat', '.sh', '.sql', '.toml']

const extOf = (it: ViewerItem | undefined) => {
  if (!it) return ''
  if (it.ext) return it.ext.toLowerCase()
  const m = it.name.match(/(\.[^.]+)$/)
  return m ? m[1].toLowerCase() : ''
}

type Kind = 'image' | 'video' | 'audio' | 'pdf' | 'text' | 'docx' | 'none'
const kind = computed<Kind>((old): Kind => {
  const e = extOf(item.value)
  if (IMG.includes(e)) return 'image'
  if (VID.includes(e)) return 'video'
  if (AUD.includes(e)) return 'audio'
  if (e === '.pdf') return 'pdf'
  if (TEXT.includes(e) || e === '.docx') return e === '.docx' ? 'docx' : 'text'
  return 'none'
})

// ── 图片：缩放/平移/旋转 + 中图档（大屏直拉原图；scale>1 换原图）──
const scale = ref(1)
const tx = ref(0)
const ty = ref(0)
const rot = ref(0)
const fullLoaded = ref(false)
const imgLoaded = ref(false)
const imgError = ref(false)
const retryNonce = ref(0)
// 档位策略：视口物理像素超过中图档宽度时直接用原图（否则 1280 档省流量）
const bigScreen = window.innerWidth * (window.devicePixelRatio || 1) > 1280
const imgSrc = computed(() => {
  if (!item.value || kind.value !== 'image') return ''
  const base = `/preview/${enc(item.value.path)}`
  if (fullLoaded.value || bigScreen) return retryNonce.value ? `${base}?_r=${retryNonce.value}` : base
  return `${base}?w=1280${retryNonce.value ? `&_r=${retryNonce.value}` : ''}`
})
function resetZoom() {
  scale.value = 1; tx.value = 0; ty.value = 0; rot.value = 0
  fullLoaded.value = false; imgLoaded.value = false; imgError.value = false; retryNonce.value = 0
}
// 倍率气泡
const zoomPct = ref(0)
let zoomBubbleTimer = 0
function showZoomBubble() {
  zoomPct.value = Math.round(scale.value * 100)
  clearTimeout(zoomBubbleTimer)
  zoomBubbleTimer = window.setTimeout(() => { zoomPct.value = 0 }, 900)
}
// 平移边界：图边缘不出舞台
function clampPan() {
  const stage = wheelRef.value
  const img = document.querySelector('.fv-img') as HTMLImageElement | null
  if (!stage || !img) return
  const swap = rot.value % 180 === 90
  const w = (swap ? img.clientHeight : img.clientWidth) * scale.value
  const h = (swap ? img.clientWidth : img.clientHeight) * scale.value
  const mx = Math.max(0, (w - stage.clientWidth) / 2)
  const my = Math.max(0, (h - stage.clientHeight) / 2)
  tx.value = Math.min(mx, Math.max(-mx, tx.value))
  ty.value = Math.min(my, Math.max(-my, ty.value))
}
// 以（可选）视口点为中心缩放：双击看细节不再跑偏
function zoomAt(factor: number, cx?: number, cy?: number) {
  const stage = wheelRef.value
  if (!stage) return
  const old = scale.value
  const next = Math.min(8, Math.max(1, old * factor))
  if (next === old) { clampPan(); return }
  if (old === 1 && next > 1) fullLoaded.value = true
  if (next === 1) {
    tx.value = 0; ty.value = 0; fullLoaded.value = false
    scale.value = 1; showZoomBubble(); return
  }
  const r = stage.getBoundingClientRect()
  const pxr = (cx ?? r.left + r.width / 2) - r.left
  const pyr = (cy ?? r.top + r.height / 2) - r.top
  const ratio = next / old
  tx.value = pxr - r.width / 2 - (pxr - r.width / 2 - tx.value) * ratio
  ty.value = pyr - r.height / 2 - (pyr - r.height / 2 - ty.value) * ratio
  scale.value = next
  clampPan(); showZoomBubble()
}
function onWheel(e: WheelEvent) {
  if (kind.value !== 'image') return
  e.preventDefault()
  zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY)
}
function onDblClick(e: MouseEvent) {
  if (kind.value !== 'image') return
  zoomAt(scale.value > 1 ? 1 / scale.value : 2.5, e.clientX, e.clientY)
}
function rotate() {
  rot.value = (rot.value + 90) % 360
  // 旋转后按舞台重新适配（竖图转横/横转竖的可视尺寸互换，允许 <1 的适配缩放）
  const img = document.querySelector('.fv-img') as HTMLImageElement | null
  const stage = wheelRef.value
  if (img && stage && scale.value === 1) {
    const sw = stage.clientWidth - 48, sh = stage.clientHeight - 48
    const iw = img.clientWidth, ih = img.clientHeight
    const fit = rot.value % 180 === 90
      ? Math.min(1, sw / ih, sh / iw)
      : Math.min(1, sw / iw, sh / ih)
    scale.value = Math.max(0.2, Math.min(8, fit))
    if (scale.value <= 1) { tx.value = 0; ty.value = 0 }
  }
}
// 双指捏合：绝对值设置（区别于 zoomAt 的增量式）
function setPinchScale(target: number) {
  const next = Math.min(8, Math.max(1, target))
  if (next > 1 && scale.value === 1) fullLoaded.value = true
  scale.value = next
  if (next === 1) { tx.value = 0; ty.value = 0; fullLoaded.value = false }
  clampPan(); showZoomBubble()
}
// 拖拽平移 + 触屏手势（单指滑动切换/下滑关闭，双指捏合缩放）
let dragging = false
let dragX = 0, dragY = 0
let touchStart: { x: number; y: number } | null = null
let swipeDx = 0, swipeDy = 0
let pinch: { dist: number; scale: number } | null = null
function onPointerDown(e: PointerEvent) {
  if (kind.value !== 'image' || scale.value === 1) return
  dragging = true; dragX = e.clientX - tx.value; dragY = e.clientY - ty.value
}
function onPointerMove(e: PointerEvent) {
  if (dragging) { tx.value = e.clientX - dragX; ty.value = e.clientY - dragY }
}
function onPointerUp() { dragging = false }
function onTouchStart(e: TouchEvent) {
  if (kind.value !== 'image') return
  if (e.touches.length === 2) {
    const [a, b] = [e.touches[0], e.touches[1]]
    pinch = { dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), scale: scale.value }
  } else if (e.touches.length === 1 && scale.value === 1) {
    touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    swipeDx = 0; swipeDy = 0
  }
}
function onTouchMove(e: TouchEvent) {
  if (kind.value !== 'image') return
  if (pinch && e.touches.length === 2) {
    const [a, b] = [e.touches[0], e.touches[1]]
    const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)
    setPinchScale(pinch.scale * d / pinch.dist)
  } else if (touchStart && e.touches.length === 1) {
    swipeDx = e.touches[0].clientX - touchStart.x
    swipeDy = e.touches[0].clientY - touchStart.y
  }
}
function onTouchEnd() {
  if (pinch) { pinch = null; return }
  if (touchStart && scale.value === 1) {
    if (Math.abs(swipeDx) > 60 && Math.abs(swipeDx) > Math.abs(swipeDy)) {
      swipeDx < 0 ? go(1) : go(-1)
    } else if (swipeDy > 90 && Math.abs(swipeDx) < 60) {
      close()
    }
  }
  touchStart = null
}

// ── 文本 / Markdown / CSV / docx 加载 ─────────────────────
const loadingText = ref(false)
const textTruncated = ref(false)
const plainText = ref('')
const htmlText = ref('')
const csvRows = ref<string[][]>([])
const textKind = ref<'plain' | 'md' | 'csv'>('plain')
const docxEl = ref<HTMLElement | null>(null)
const MAX_CHARS = 1_000_000

async function loadTextLike() {
  const it = item.value
  if (!it || kind.value !== 'text' && kind.value !== 'docx') return
  loadingText.value = true
  plainText.value = ''; htmlText.value = ''; csvRows.value = []; textTruncated.value = false
  if (docxEl.value) docxEl.value.innerHTML = ''
  const e = extOf(it)
  try {
    if (kind.value === 'docx') {
      await nextTick()
      const { renderAsync } = await import('docx-preview')
      const buf = await fetch(`/preview/${enc(it.path)}`).then(r => r.arrayBuffer())
      if (item.value === it && docxEl.value) {
        await renderAsync(buf, docxEl.value, undefined, { inWrapper: false })
      }
    } else {
      const { hljs, marked, purify } = await textLibs()
      let raw = await fetch(`/preview/${enc(it.path)}`).then(r => r.text())
      if (raw.length > MAX_CHARS) { raw = raw.slice(0, MAX_CHARS); textTruncated.value = true }
      if (e === '.md') {
        textKind.value = 'md'
        htmlText.value = purify.sanitize(marked.parse(raw, { async: false }) as string)
      } else if (e === '.csv') {
        textKind.value = 'csv'
        csvRows.value = raw.split(/\r?\n/).slice(0, 500).map(l => l.split(','))
      } else {
        textKind.value = 'plain'
        const lang = hljs.getLanguage(e.slice(1)) ? e.slice(1) : undefined
        const lines = raw.replace(/\n$/, '').split('\n')
        const capped = lines.slice(0, 5000)
        if (lines.length > 5000) textTruncated.value = true
        const html = lang
          ? hljs.highlight(capped.join('\n'), { language: lang }).value
          : hljs.highlightAuto(capped.join('\n')).value
        plainText.value = html
      }
    }
  } catch (err) {
    toast('预览加载失败：' + (err as Error).message, 'err')
  } finally { loadingText.value = false }
}

// ── 音频连播 ──────────────────────────────────────────────
const audioEl = ref<HTMLAudioElement | null>(null)
const audioPlaying = ref(false)
const audioCur = ref(0)
const audioDur = ref(0)
function fmtTime(s: number) {
  if (!isFinite(s)) return '0:00'
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}
function toggleAudio() {
  const a = audioEl.value
  if (!a) return
  a.paused ? a.play().catch(() => {}) : a.pause()
}
function onAudioEnded() {
  // 连播：向后找下一个音频
  for (let i = props.modelValue + 1; i < props.items.length; i++) {
    if (AUD.includes(extOf(props.items[i]))) { go(i - props.modelValue); return }
  }
  audioPlaying.value = false
}
watch(audioEl, (a) => {
  if (a && kind.value === 'audio') a.play().catch(() => {})
})

// ── 视频 ─────────────────────────────────────────────────
const videoEl = ref<HTMLVideoElement | null>(null)
const videoSpeed = ref(1)
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2]
function setSpeed(s: number) {
  videoSpeed.value = s
  if (videoEl.value) videoEl.value.playbackRate = s
}
async function pip() {
  try {
    const v = videoEl.value as (HTMLVideoElement & { requestPictureInPicture?: () => Promise<unknown> }) | null
    if (v && document.pictureInPictureElement) await document.exitPictureInPicture()
    else if (v?.requestPictureInPicture) await v.requestPictureInPicture()
  } catch { toast('画中画不可用', 'err') }
}

// ── 导航 ─────────────────────────────────────────────────
function go(delta: number) {
  const next = props.modelValue + delta
  if (next < 0 || next >= props.items.length) return
  emit('update:modelValue', next)
}
const canPrev = computed(() => props.modelValue > 0)
const canNext = computed(() => props.modelValue < props.items.length - 1)

// ── 信息面板 ─────────────────────────────────────────────
const showInfo = ref(false)
const metaInfo = ref<{ width: number | null; height: number | null; camera: string | null } | null>(null)
async function loadMeta() {
  const it = item.value
  metaInfo.value = null
  if (!it || kind.value !== 'image') return
  try { metaInfo.value = await api.photoMeta(it.path) } catch { /* 无元数据不阻塞预览 */ }
}
const renaming = ref(false)
const renameName = ref('')
const sharePath = ref<string[] | null>(null)
async function doRename() {
  const it = item.value
  if (!it || !renameName.value.trim()) return
  try {
    await api.rename(it.path, renameName.value.trim())
    toast('已重命名', 'ok')
    renaming.value = false
    emit('refresh')
    close()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function doDelete() {
  const it = item.value
  if (!it) return
  if (!(await confirmDlg(`删除「${it.name}」？（可从回收站恢复）`, { danger: true, okText: '删除' }))) return
  try {
    await api.remove(it.path)
    toast('已删除，可在回收站恢复', 'ok')
    emit('refresh')
    close()
  } catch (e) { toast((e as Error).message, 'err') }
}
function doShare() {
  const it = item.value
  if (!it) return
  sharePath.value = [it.path]
}

// ── 全屏（⛶：隐藏工具条沉浸看图；空白点击先退全屏）────────
const isFs = ref(false)
async function toggleFs() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.querySelector('.fv-overlay')?.requestFullscreen()
  } catch { toast('当前环境不支持全屏', 'err') }
}
function onFsChange() { isFs.value = !!document.fullscreenElement }

// ── 全局键盘 / 滚轮 / 滚动锁定 ────────────────────────────
function onKey(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  if (sharePath.value) { if (e.key === 'Escape') sharePath.value = null; return }
  if (e.key === 'Escape') { showInfo.value ? showInfo.value = false : close() }
  else if (e.key === 'ArrowLeft') go(-1)
  else if (e.key === 'ArrowRight') go(1)
  else if (e.key === ' ') {
    if (kind.value === 'video' && videoEl.value) { e.preventDefault(); videoEl.value.paused ? videoEl.value.play() : videoEl.value.pause() }
    else if (kind.value === 'audio') { e.preventDefault(); toggleAudio() }
  }
  else if (e.key === 'i' || e.key === 'I') showInfo.value = !showInfo.value
  else if (kind.value === 'image' && (e.key === '+' || e.key === '=')) zoomAt(1.25)
  else if (kind.value === 'image' && e.key === '-') zoomAt(1 / 1.25)
}
const wheelRef = ref<HTMLElement | null>(null)
// 切换文件：重置缩放/播放器状态，文本类按需加载内容，图片拉元数据
// （必须在所有 ref 声明之后注册——immediate 回调同步执行，放前面会踩 TDZ）
watch(() => props.modelValue, () => {
  resetZoom()
  audioPlaying.value = false; audioCur.value = 0; audioDur.value = 0
  videoSpeed.value = 1
  void loadTextLike()
  void loadMeta()
}, { immediate: true })
onMounted(() => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('resize', clampPan)
  document.addEventListener('fullscreenchange', onFsChange)
  wheelRef.value?.addEventListener('wheel', onWheel, { passive: false })
  document.body.style.overflow = 'hidden'
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', clampPan)
  document.removeEventListener('fullscreenchange', onFsChange)
  clearTimeout(zoomBubbleTimer)
  wheelRef.value?.removeEventListener('wheel', onWheel)
  document.body.style.overflow = ''
})
watch(wheelRef, (el, old) => {
  if (old) old.removeEventListener('wheel', onWheel)
  if (el) el.addEventListener('wheel', onWheel, { passive: false })
})

function close() { emit('close') }

// 空白点击：有信息面板先收面板，否则退出预览（点在媒体/按钮/正文上不触发）
function onBlankClick(e: MouseEvent) {
  if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return }
  const t = e.target as HTMLElement
  if (t === e.currentTarget || t.classList.contains('fv-center')
      || t.classList.contains('fv-audio')) {
    if (showInfo.value) showInfo.value = false
    else close()
  }
}

// ── 胶片条（桌面端）───────────────────────────────────────
const filmstrip = computed(() => {
  const lo = Math.max(0, props.modelValue - 30)
  const hi = Math.min(props.items.length, props.modelValue + 31)
  return props.items.slice(lo, hi).map((it, i) => ({ it, index: lo + i }))
})
function thumbOf(it: ViewerItem): string | null {
  const e = extOf(it)
  if (IMG.includes(e) || VID.includes(e)) return `/thumb/${enc(it.path)}`
  return null
}
function iconOf(it: ViewerItem): string {
  const e = extOf(it)
  if (AUD.includes(e)) return '🎵'
  if (e === '.pdf') return '📕'
  if (TEXT.includes(e)) return '📄'
  return '📦'
}
</script>

<template>
  <teleport to="body">
    <div class="fv-overlay" @click.self="close">
      <div class="fv-shell" :class="{ 'with-info': showInfo }">
        <!-- 顶栏 -->
        <header class="fv-top">
          <div class="fv-title">
            <span class="fv-name" :title="item?.name">{{ item?.name }}</span>
            <span v-if="item?.size_human" class="fv-badge">{{ item.size_human }}</span>
            <span class="fv-badge">{{ extOf(item).replace('.', '').toUpperCase() || '文件' }}</span>
          </div>
          <div class="fv-ops">
            <template v-if="kind === 'image'">
              <button class="fv-btn" title="缩小 (-)" @click="zoomAt(1 / 1.25)">－</button>
              <button class="fv-btn" title="放大 (+)" @click="zoomAt(1.25)">＋</button>
              <button class="fv-btn" title="旋转" @click="rotate">↻</button>
              <button class="fv-btn" title="适应窗口 (1:1)" @click="resetZoom">1:1</button>
            </template>
            <template v-if="kind === 'video'">
              <select class="fv-btn fv-speed" :value="videoSpeed" @change="setSpeed(Number(($event.target as HTMLSelectElement).value))" title="倍速">
                <option v-for="s in SPEEDS" :key="s" :value="s">{{ s }}x</option>
              </select>
              <button class="fv-btn" title="画中画" @click="pip">⧉</button>
            </template>
            <button class="fv-btn" title="全屏" @click="toggleFs">⛶</button>
            <button class="fv-btn" :class="{ on: showInfo }" title="信息 (i)" @click="showInfo = !showInfo">ℹ</button>
            <a class="fv-btn" :href="`/files/${enc(item?.path || '')}`" target="_blank" title="下载">⬇</a>
            <button class="fv-btn" title="分享" @click="doShare">🔗</button>
            <button class="fv-btn" title="关闭 (Esc)" @click="close">✕</button>
          </div>
        </header>

        <!-- 内容区（点空白：收信息面板 / 退出预览） -->
        <div class="fv-stage" ref="wheelRef" @click="onBlankClick"
             @pointerdown="onPointerDown" @pointermove="onPointerMove"
             @pointerup="onPointerUp" @pointercancel="onPointerUp"
             @touchstart="onTouchStart" @touchmove="onTouchMove" @touchend="onTouchEnd">
          <button v-if="canPrev" class="fv-nav prev" @click="go(-1)">‹</button>
          <button v-if="canNext" class="fv-nav next" @click="go(1)">›</button>
          <span class="fv-count">{{ modelValue + 1 }} / {{ items.length }}</span>
          <div v-if="zoomPct" class="fv-zoom-bubble">{{ zoomPct }}%</div>

          <!-- 图片 -->
          <div v-if="kind === 'image'" class="fv-center">
            <div v-if="imgError" class="fv-unsupported">
              <div class="fv-uns-icon">🖼️</div>
              <b>图片加载失败</b>
              <button class="btn sm" @click="retryNonce++; imgError = false">↻ 重试</button>
            </div>
            <template v-else>
              <div v-if="!imgLoaded" class="fv-loading"><span class="fv-spin" /></div>
              <img :key="item?.path + imgSrc" :src="imgSrc" class="fv-img"
                   :style="{ transform: `translate(${tx}px, ${ty}px) scale(${scale}) rotate(${rot}deg)`, cursor: scale > 1 ? 'grab' : 'zoom-in' }"
                   :class="{ dragging }"
                   @dblclick="onDblClick" @load="imgLoaded = true" @error="imgError = true" draggable="false" />
            </template>
          </div>

          <!-- 视频 -->
          <div v-else-if="kind === 'video'" class="fv-center">
            <video ref="videoEl" :key="item?.path" :src="`/preview/${enc(item?.path || '')}`"
                   class="fv-media" controls autoplay playsinline @loadedmetadata="setSpeed(videoSpeed)" />
          </div>

          <!-- 音频 -->
          <div v-else-if="kind === 'audio'" class="fv-center fv-audio">
            <div class="fv-audio-disc" :class="{ spin: audioPlaying }">{{ audioPlaying ? '♪' : '🎵' }}</div>
            <div class="fv-audio-name">{{ item?.name }}</div>
            <audio ref="audioEl" :key="item?.path" :src="`/preview/${enc(item?.path || '')}`"
                   @play="audioPlaying = true" @pause="audioPlaying = false"
                   @timeupdate="audioCur = audioEl?.currentTime || 0"
                   @loadedmetadata="audioDur = audioEl?.duration || 0" @ended="onAudioEnded" />
            <div class="fv-audio-bar">
              <button class="fv-btn big" @click="toggleAudio">{{ audioPlaying ? '⏸' : '▶' }}</button>
              <span class="fv-time">{{ fmtTime(audioCur) }}</span>
              <input type="range" min="0" :max="audioDur || 0" step="0.1" :value="audioCur"
                     @input="audioEl && (audioEl.currentTime = Number(($event.target as HTMLInputElement).value))" />
              <span class="fv-time">{{ fmtTime(audioDur) }}</span>
            </div>
            <p class="fv-hint">目录内音频会自动连播（←/→ 也可切歌）</p>
          </div>

          <!-- PDF -->
          <div v-else-if="kind === 'pdf'" class="fv-center fv-wide">
            <iframe :key="item?.path" :src="`/preview/${enc(item?.path || '')}`" class="fv-iframe" />
          </div>

          <!-- docx -->
          <div v-else-if="kind === 'docx'" class="fv-center fv-wide fv-scroll">
            <div v-if="loadingText" class="fv-loading"><span class="fv-spin" /></div>
            <div ref="docxEl" class="fv-docx" />
          </div>

          <!-- 文本 / 代码 / Markdown / CSV -->
          <div v-else-if="kind === 'text'" class="fv-center fv-wide fv-scroll">
            <div v-if="loadingText" class="fv-loading"><span class="fv-spin" /></div>
            <template v-else>
              <p v-if="textTruncated" class="fv-trunc">⚠ 文件较大，仅显示前一部分</p>
              <article v-if="textKind === 'md'" class="fv-prose" v-html="htmlText" />
              <table v-else-if="textKind === 'csv'" class="fv-csv">
                <tr v-for="(row, ri) in csvRows" :key="ri">
                  <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
                </tr>
              </table>
              <pre v-else class="fv-code"><code v-html="plainText" /></pre>
            </template>
          </div>

          <!-- 不支持 -->
          <div v-else class="fv-center">
            <div class="fv-unsupported">
              <div class="fv-uns-icon">{{ iconOf(item || { path: '', name: '' }) }}</div>
              <b>{{ item?.name }}</b>
              <p>「{{ extOf(item).replace('.', '').toUpperCase() }}」格式暂不支持在线预览</p>
              <a class="btn sm" :href="`/files/${enc(item?.path || '')}`" target="_blank">⬇ 下载查看</a>
            </div>
          </div>
        </div>

        <!-- 胶片条（桌面端） -->
        <footer v-if="items.length > 1" class="fv-strip">
          <div v-for="f in filmstrip" :key="f.it.path" class="fv-thumb"
               :class="{ cur: f.index === modelValue }" @click="emit('update:modelValue', f.index)">
            <img v-if="thumbOf(f.it)" :src="thumbOf(f.it)!" loading="lazy" alt="" />
            <span v-else>{{ iconOf(f.it) }}</span>
          </div>
        </footer>
      </div>

      <!-- 信息面板 -->
      <transition name="fv-drawer">
        <aside v-if="showInfo && item" class="fv-info" @click.stop>
          <div class="fv-info-head">
            <h3>文件信息</h3>
            <button class="fv-btn" title="收起" @click="showInfo = false">✕</button>
          </div>
          <dl>
            <dt>名称</dt><dd :title="item.name">{{ item.name }}</dd>
            <dt>路径</dt><dd :title="item.path">{{ item.path }}</dd>
            <dt>大小</dt><dd>{{ item.size_human || human(item.size ?? null) }}</dd>
            <template v-if="metaInfo">
              <dt>原始尺寸</dt>
              <dd>{{ metaInfo.width && metaInfo.height ? `${metaInfo.width} × ${metaInfo.height}` : '未知' }}</dd>
              <template v-if="metaInfo.camera">
                <dt>拍摄设备</dt><dd>{{ metaInfo.camera }}</dd>
              </template>
            </template>
            <dt v-if="item.modified">修改时间</dt><dd v-if="item.modified">{{ item.modified.slice(0, 16).replace('T', ' ') }}</dd>
            <template v-if="item.taken">
              <dt>拍摄日期</dt><dd>{{ item.taken }}</dd>
              <dt>上传者</dt><dd>{{ item.uploader }}</dd>
            </template>
          </dl>
          <div class="fv-info-ops">
            <a class="btn ghost sm" :href="`/files/${enc(item.path)}`" target="_blank">⬇ 下载</a>
            <button class="btn ghost sm" @click="doShare">🔗 分享</button>
            <button class="btn ghost sm" @click="renaming = true; renameName = item.name">✏️ 重命名</button>
            <button class="btn danger sm" @click="doDelete">🗑️ 删除</button>
          </div>
          <p class="fv-hint">关闭预览后操作立即生效（重命名/删除会刷新列表）</p>
        </aside>
      </transition>

      <!-- 预览内分享弹窗 -->
      <Modal v-if="sharePath" title="创建分享" @close="sharePath = null">
        <ShareModal :paths="sharePath" @close="sharePath = null" />
      </Modal>

      <!-- 预览内重命名 -->
      <Modal v-if="renaming && item" title="重命名" @close="renaming = false">
        <input v-model="renameName" style="width:100%" @keydown.enter="doRename" />
        <div class="actions">
          <button class="btn ghost sm" @click="renaming = false">取消</button>
          <button class="btn sm" @click="doRename">确定</button>
        </div>
      </Modal>
    </div>
  </teleport>
</template>

<style>
.fv-overlay {
  position: fixed; inset: 0; z-index: 300; display: grid; place-items: center;
  background: rgba(8, 8, 14, .88); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
  animation: fv-fade .18s ease;
}
@keyframes fv-fade { from { opacity: 0 } }
@keyframes fv-pop { from { opacity: 0; transform: scale(.96) } }
.fv-shell {
  display: flex; flex-direction: column; width: 100%; height: 100%;
  animation: fv-pop .18s ease;
}
.fv-top {
  display: flex; align-items: center; gap: 12px; padding: 10px 16px;
  background: rgba(16, 16, 26, .72); border-bottom: 1px solid rgba(255, 255, 255, .08);
  flex: none;
}
.fv-title { display: flex; align-items: center; gap: 8px; min-width: 0; color: #fff; }
.fv-name { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fv-badge {
  flex: none; font-size: 11px; color: rgba(255, 255, 255, .65);
  border: 1px solid rgba(255, 255, 255, .18); border-radius: 999px; padding: 2px 8px;
}
.fv-ops { margin-left: auto; display: flex; gap: 4px; align-items: center; }
.fv-btn {
  border: 0; background: transparent; color: rgba(255, 255, 255, .82); cursor: pointer;
  width: 32px; height: 32px; border-radius: 9px; font-size: 15px; text-decoration: none;
  display: inline-grid; place-items: center; transition: background .12s;
}
.fv-btn:hover { background: rgba(255, 255, 255, .12); }
.fv-btn.on { background: rgba(255, 255, 255, .18); }
.fv-btn.big { width: 44px; height: 44px; font-size: 19px; }
.fv-speed { width: auto; padding: 0 6px; font-size: 12.5px; }

.fv-stage { position: relative; flex: 1; min-height: 0; overflow: hidden; }
/* flex 而非 grid：grid 的 auto 轨道会让 max-height:100% 失效（竖屏图溢出） */
.fv-center {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  padding: 28px; min-width: 0; min-height: 0;
}
.fv-wide { padding: 16px; }
.fv-scroll { display: block; overflow: auto; padding: 16px; }
.fv-loading { display: grid; place-items: center; }
.fv-spin {
  width: 34px; height: 34px; border-radius: 50%;
  border: 3px solid rgba(255, 255, 255, .18); border-top-color: #fff;
  animation: fv-rot .8s linear infinite;
}
@keyframes fv-rot { to { transform: rotate(360deg) } }

.fv-img {
  max-width: 100%; max-height: 100%; object-fit: contain; user-select: none;
  box-shadow: 0 12px 60px rgba(0, 0, 0, .6); border-radius: 6px;
  transition: transform .12s ease-out; will-change: transform;
}
.fv-img.dragging { transition: none; cursor: grabbing; }
.fv-media { max-width: 100%; max-height: 100%; border-radius: 10px; box-shadow: 0 12px 60px rgba(0, 0, 0, .6); }

.fv-nav {
  position: absolute; top: 50%; transform: translateY(-50%); z-index: 5;
  width: 44px; height: 64px; border: 0; border-radius: 12px; cursor: pointer;
  background: rgba(255, 255, 255, .08); color: #fff; font-size: 30px; line-height: 1;
  backdrop-filter: blur(8px); transition: background .12s;
}
.fv-nav:hover { background: rgba(255, 255, 255, .2); }
.fv-nav.prev { left: 12px; }
.fv-nav.next { right: 12px; }
.fv-count {
  position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 5;
  font-size: 12px; color: rgba(255, 255, 255, .6); background: rgba(0, 0, 0, .35);
  border-radius: 999px; padding: 3px 12px;
}
.fv-zoom-bubble {
  position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%); z-index: 6;
  font-size: 13px; font-weight: 600; color: #fff; background: rgba(0, 0, 0, .5);
  border: 1px solid rgba(255, 255, 255, .18); border-radius: 999px; padding: 4px 14px;
  animation: fv-fade .15s ease; pointer-events: none;
}

/* 全屏沉浸：隐藏工具条与胶片条 */
.fv-overlay:fullscreen { background: #000; }
.fv-overlay:fullscreen .fv-top,
.fv-overlay:fullscreen .fv-strip { display: none; }

.fv-iframe { width: 100%; height: 100%; border: 0; border-radius: 10px; background: #fff; }
.fv-docx { background: #fff; border-radius: 8px; padding: 24px 32px; max-width: 900px; margin: 0 auto; color: #222; }
.fv-code {
  margin: 0; padding: 18px 20px; border-radius: 12px; background: #0d1117;
  font-size: 13px; line-height: 1.55; color: #e6edf3; overflow: auto; width: 100%;
  box-shadow: 0 8px 40px rgba(0, 0, 0, .5);
}
.fv-trunc { color: #ffb224; font-size: 12.5px; margin: 0 0 8px; }
.fv-csv { border-collapse: collapse; background: rgba(255, 255, 255, .03); font-size: 13px; color: #e6edf3; margin: 0 auto; }
.fv-csv td { border: 1px solid rgba(255, 255, 255, .12); padding: 6px 12px; white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
.fv-csv tr:first-child td { background: rgba(255, 255, 255, .08); font-weight: 600; }

.fv-prose {
  color: #e8e8f0; font-size: 14.5px; line-height: 1.75; max-width: 860px; width: 100%;
  background: rgba(255, 255, 255, .04); border-radius: 12px; padding: 28px 34px; margin: 0 auto;
}
.fv-prose h1, .fv-prose h2, .fv-prose h3 { color: #fff; margin: 1.1em 0 .5em; }
.fv-prose h1 { font-size: 1.6em; } .fv-prose h2 { font-size: 1.35em; }
.fv-prose a { color: #9ab8ff; }
.fv-prose code { background: rgba(255, 255, 255, .1); border-radius: 5px; padding: 2px 6px; font-size: .88em; }
.fv-prose pre { background: #0d1117; border-radius: 10px; padding: 14px 16px; overflow: auto; }
.fv-prose pre code { background: transparent; padding: 0; }
.fv-prose blockquote { border-left: 3px solid rgba(255, 255, 255, .25); margin: .8em 0; padding: .2em 1em; color: #b9b9c8; }
.fv-prose img { max-width: 100%; border-radius: 8px; }
.fv-prose table { border-collapse: collapse; } .fv-prose td, .fv-prose th { border: 1px solid rgba(255,255,255,.18); padding: 5px 12px; }

/* 音频播放器 */
.fv-audio { display: flex; flex-direction: column; gap: 14px; align-content: center; justify-items: center; }
.fv-audio-disc {
  width: 110px; height: 110px; border-radius: 50%; display: grid; place-items: center; font-size: 42px;
  background: radial-gradient(circle at 32% 30%, #5b6bd6, #23244d 70%);
  box-shadow: 0 14px 50px rgba(40, 50, 140, .5);
}
.fv-audio-disc.spin { animation: fv-rot 5s linear infinite; }
.fv-audio-name { color: #fff; font-weight: 600; max-width: 80vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fv-audio-bar { display: flex; align-items: center; gap: 10px; width: min(560px, 86vw); }
.fv-audio-bar input[type=range] { flex: 1; accent-color: #7c8cf8; }
.fv-time { font-size: 12px; color: rgba(255, 255, 255, .6); font-variant-numeric: tabular-nums; }
.fv-hint { font-size: 12px; color: rgba(255, 255, 255, .4); margin: 0; }

/* 胶片条 */
.fv-strip {
  flex: none; display: flex; gap: 6px; overflow-x: auto; padding: 8px 14px;
  background: rgba(16, 16, 26, .72); border-top: 1px solid rgba(255, 255, 255, .08);
}
.fv-thumb {
  flex: none; width: 52px; height: 52px; border-radius: 9px; overflow: hidden; cursor: pointer;
  display: grid; place-items: center; font-size: 20px; color: rgba(255, 255, 255, .7);
  background: rgba(255, 255, 255, .06); border: 2px solid transparent; opacity: .62;
  transition: opacity .12s, border-color .12s;
}
.fv-thumb:hover { opacity: 1; }
.fv-thumb.cur { border-color: #7c8cf8; opacity: 1; }
.fv-thumb img { width: 100%; height: 100%; object-fit: cover; }

/* 信息面板 */
.fv-info {
  position: fixed; top: 0; right: 0; bottom: 0; width: min(320px, 88vw); z-index: 310;
  background: rgba(18, 18, 30, .92); backdrop-filter: blur(20px);
  border-left: 1px solid rgba(255, 255, 255, .1); padding: 20px 20px 12px; overflow: auto;
}
.fv-info h3 { margin: 0; color: #fff; font-size: 15px; }
.fv-info-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.fv-info dl { display: grid; grid-template-columns: 64px 1fr; gap: 8px 10px; margin: 0 0 18px; }
.fv-info dt { color: rgba(255, 255, 255, .45); font-size: 12.5px; }
.fv-info dd { margin: 0; color: #e8e8f0; font-size: 13px; word-break: break-all; }
.fv-info-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.fv-info .btn { justify-content: center; }
@keyframes fv-drawer-in { from { transform: translateX(30px); opacity: 0 } }
.fv-drawer-enter-active { animation: fv-drawer-in .18s ease; }

.fv-unsupported { display: grid; justify-items: center; gap: 10px; color: rgba(255, 255, 255, .85); text-align: center; }
.fv-uns-icon { font-size: 58px; }
.fv-unsupported p { margin: 0; color: rgba(255, 255, 255, .5); font-size: 13px; }

@media (max-width: 640px) {
  .fv-strip { display: none; }
  .fv-top { padding: 8px 10px; gap: 8px; }
  .fv-badge { display: none; }
  .fv-center { padding: 14px; }
  .fv-count { top: 6px; }
}
@media (prefers-reduced-motion: reduce) {
  .fv-overlay, .fv-shell, .fv-drawer-enter-active { animation: none; }
  .fv-img { transition: none; }
}
</style>
