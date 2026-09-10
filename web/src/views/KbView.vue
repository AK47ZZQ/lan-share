<script setup lang="ts">
// 知识图谱（v0.24 M-K4）：与 App 端同规格的 Canvas 2D 手绘力导向图。
// · 服务端预布局（坐标随 /api/kb/graph 下发），web 端零仿真冷启动
// · 交互：滚轮缩放（围绕光标）/ 拖空白平移 / 拖节点局部增量 / 点按选中卡片 /
//   悬停高亮一跳邻居 / 搜索定位脉冲 / 适配视图
// · 节点落点：FileViewer 预览（双链点击经 kbOpen 事件在页内跳转）
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, type KbContext, type KbGraphData, type KbName, type KbNode, type Me } from '../api'
import { categoryHue, resolveKbTarget } from '../kb'
import DirPicker from '../components/DirPicker.vue'
import FileViewer, { type ViewerItem } from '../components/FileViewer.vue'
import Modal from '../components/Modal.vue'
import { toast } from '../ui'

const props = defineProps<{ me: Me }>()

const root = ref(localStorage.getItem('kb_root') ?? '')
const graph = ref<KbGraphData | null>(null)
const names = ref<KbName[]>([])
const loading = ref(true)
const error = ref('')
// 筛选（与 App 筛选抽屉同款语义）
const fCategory = ref('')
const fTag = ref('')
const fHideOrphans = ref(false)
const fHideGhosts = ref(false)
const fGhostsOnly = ref(false)
const query = ref('')
const selectedId = ref<string | null>(null)
const hoverId = ref<string | null>(null)
// 视口：screen = graph * scale + origin
const scale = ref(1)
const origin = ref({ x: 0, y: 0 })
const previewItem = ref<ViewerItem | null>(null)
const showRootPicker = ref(false)
const showFilter = ref(false)
// 反链面板（review P2-4）：节点卡内"谁链到了这篇"
interface BlItem { path: string; name: string; excerpt?: string; ghost: boolean }
const bl = ref<{ backlinks: BlItem[]; outgoing: BlItem[] } | null>(null)
const blLoading = ref(false)
// v0.25 点按=显示内容：选中笔记节点拉 /api/kb/context（正文头 + abs_path）
const kbCtx = ref<KbContext | null>(null)
const ctxLoading = ref(false)
async function loadCtx() {
  const n = selected.value
  if (!n || n.ghost || n.external || n.kind === 'folder' || !n.path) { kbCtx.value = null; return }
  ctxLoading.value = true
  try { kbCtx.value = await api.kbContext(root.value, n.path) } catch { kbCtx.value = null } finally { ctxLoading.value = false }
}
const ctxExcerpt = computed(() => {
  const c = kbCtx.value?.note.content ?? ''
  return c.length > 320 ? c.slice(0, 320) + '…' : c
})
/** 分类枢纽的直属成员（含子分类下的笔记，按名排序） */
function folderChildren(f: KbNode): KbNode[] {
  return (graph.value?.nodes ?? [])
    .filter(m => m.kind !== 'folder' && !m.ghost &&
      (m.category === f.path || m.category.startsWith(f.path + '/')))
    .sort((a, b) => a.name.localeCompare(b.name))
}
/** 点分类枢纽 → 只看该分类（复用分类过滤器） */
function pickFolder(f: KbNode) {
  fCategory.value = f.path
  selectedId.value = null
  fitView()
  scheduleDraw()
}

const canvasRef = ref<HTMLCanvasElement | null>(null)
let ctx: CanvasRenderingContext2D | null = null
let raf = 0
let pulseT = -1 // 搜索脉冲动画进度（<0 关闭）
let pulseId: string | null = null

const K = 130 // 期望间距（与 App/服务端同常数）

interface VM { n: KbNode; hue: number; color: string }
const byId = computed(() => {
  const m = new Map<string, KbNode>()
  for (const n of graph.value?.nodes ?? []) m.set(n.id, n)
  return m
})
const categories = computed(() =>
  [...new Set((graph.value?.nodes ?? []).filter(n => !n.ghost && !n.external && n.category).map(n => n.category))].sort())
const tags = computed(() => {
  const s = new Set<string>()
  for (const n of graph.value?.nodes ?? []) for (const t of n.tags) s.add(t)
  return [...s].sort()
})
const visible = computed(() => {
  const g = graph.value
  if (!g) return [] as KbNode[]
  const q = query.value.trim().toLowerCase()
  return g.nodes.filter(n => {
    if (fGhostsOnly.value) { if (!n.ghost) return false }
    else {
      if (fHideGhosts.value && n.ghost) return false
      if (fHideOrphans.value && n.orphan) return false
    }
    if (fCategory.value) {
      if (n.category !== fCategory.value && !n.category.startsWith(fCategory.value + '/')) return false
    }
    if (fTag.value && !n.tags.includes(fTag.value)) return false
    if (q && !n.name.toLowerCase().includes(q) &&
        !n.aliases.some(a => a.toLowerCase().includes(q))) return false
    return true
  })
})
const visibleIds = computed(() => new Set(visible.value.map(n => n.id)))
const selected = computed(() => (selectedId.value ? byId.value.get(selectedId.value) ?? null : null))
const statsLine = computed(() => {
  const g = graph.value
  if (!g) return ''
  return `${visible.value.length}/${g.stats.nodes} 节点 · ${g.stats.edges} 边`
    + (g.stats.ghosts ? ` · 幽灵 ${g.stats.ghosts}` : '')
})
const filterOn = computed(() =>
  fCategory.value || fTag.value || fHideOrphans.value || fHideGhosts.value || fGhostsOnly.value || query.value)

const hueColor = (hue: number): string => `hsl(${hue} 55% 58%)`

function nodeColor(n: KbNode): string {
  if (n.kind === 'folder') return 'var(--accent)'
  if (n.ghost) return 'rgba(128,128,140,0.5)'
  if (n.external) return 'var(--accent-2)'
  return hueColor(categoryHue(n.category))
}
function nodeRadius(n: KbNode): number {
  if (n.kind === 'folder') return 11
  if (n.ghost) return 7
  if (n.external) return 6
  return 8 + Math.min(8, (n.out + n.ind) * 1.2)
}

async function load() {
  if (!root.value) { loading.value = false; return }
  loading.value = true
  error.value = ''
  try {
    const [g, nm] = await Promise.all([api.kbGraph(root.value), api.kbNames(root.value).catch(() => [] as KbName[])])
    graph.value = g
    names.value = nm
    localStorage.setItem('kb_root', root.value)
    selectedId.value = null
    hoverId.value = null
    await nextTick()
    fitView()
    scheduleDraw()
  } catch (e) {
    error.value = (e as Error).message
    graph.value = null
  } finally {
    loading.value = false
  }
}

// ── 画布绘制 ──
function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function toGraph(px: number, py: number) {
  return { x: (px - origin.value.x) / scale.value, y: (py - origin.value.y) / scale.value }
}

function hitNode(px: number, py: number): KbNode | null {
  const p = toGraph(px, py)
  let best: KbNode | null = null
  let bestD = Infinity
  for (const n of visible.value) {
    const d = Math.hypot(n.x - p.x, n.y - p.y)
    const r = nodeRadius(n) + 12 / scale.value
    if (d < r && d < bestD) { bestD = d; best = n }
  }
  return best
}

function fitView() {
  const cv = canvasRef.value
  const vis = visible.value
  if (!cv || !vis.length) return
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const n of vis) {
    minX = Math.min(minX, n.x); minY = Math.min(minY, n.y)
    maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y)
  }
  const w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY)
  const s = Math.min(1.6, Math.min(cv.clientWidth * 0.9 / w, cv.clientHeight * 0.85 / h))
  scale.value = Math.max(0.08, Math.min(1.6, s))
  origin.value = {
    x: cv.clientWidth / 2 - (minX + maxX) / 2 * scale.value,
    y: cv.clientHeight / 2 - (minY + maxY) / 2 * scale.value,
  }
  scheduleDraw()
}

const labelCache = new Map<string, { w: number; text: string }>()
function draw() {
  const cv = canvasRef.value
  if (!cv) return
  const dpr = window.devicePixelRatio || 1
  const W = cv.clientWidth, H = cv.clientHeight
  if (cv.width !== W * dpr || cv.height !== H * dpr) {
    cv.width = W * dpr; cv.height = H * dpr
  }
  const c = ctx ?? (ctx = cv.getContext('2d'))
  if (!c) return
  c.setTransform(dpr, 0, 0, dpr, 0, 0)
  c.clearRect(0, 0, W, H)

  const g = graph.value
  if (!g) return
  const vis = visibleIds.value
  const text = cssVar('--text', '#23263d')
  const line = cssVar('--line', 'rgba(35,38,61,0.08)')
  const accent = cssVar('--accent', '#5b6cff')
  const dimEdge = visible.value.length > 800
  const showLabels = visible.value.length <= 300
  const hoverN = hoverId.value ? byId.value.get(hoverId.value) : null
  const selN = selectedId.value ? byId.value.get(selectedId.value) : null
  const hlIds = new Set<string>()
  const focus = hoverN ?? selN
  if (focus) {
    hlIds.add(focus.id)
    for (const e of g.edges) {
      if (e[0] === focus.id) hlIds.add(e[1])
      if (e[1] === focus.id) hlIds.add(e[0])
    }
  }
  c.save()
  c.translate(origin.value.x, origin.value.y)
  c.scale(scale.value, scale.value)
  // 边
  for (const e of g.edges) {
    const s = byId.value.get(e[0]), t = byId.value.get(e[1])
    if (!s || !t || !vis.has(e[0]) || !vis.has(e[1])) continue
    const hl = focus && (e[0] === focus.id || e[1] === focus.id)
    c.strokeStyle = hl ? accent : line
    c.globalAlpha = hl ? 0.75 : dimEdge ? 0.5 : 1
    c.lineWidth = (hl ? 1.8 : 1.1) / scale.value
    c.beginPath(); c.moveTo(s.x, s.y); c.lineTo(t.x, t.y); c.stroke()
  }
  c.globalAlpha = 1
  // 节点
  for (const n of visible.value) {
    const hl = !focus || hlIds.has(n.id)
    c.globalAlpha = hl ? 1 : 0.15
    const r = nodeRadius(n)
    if (n.ghost) {
      c.strokeStyle = text; c.globalAlpha *= 0.45
      c.lineWidth = 1.4 / scale.value
      c.setLineDash([3 / scale.value, 3 / scale.value])
      c.beginPath(); c.arc(n.x, n.y, r, 0, Math.PI * 2); c.stroke()
      c.setLineDash([])
    } else {
      c.fillStyle = n.external ? cssVar('--accent-2', '#8a5bff') : hueColor(categoryHue(n.category))
      c.beginPath(); c.arc(n.x, n.y, r, 0, Math.PI * 2); c.fill()
      if (n.external) {
        c.strokeStyle = c.fillStyle as string
        c.lineWidth = 1.2 / scale.value
        c.beginPath(); c.arc(n.x, n.y, r + 2.5 / scale.value, 0, Math.PI * 2); c.stroke()
      }
    }
    if (n.id === selectedId.value || n.id === pulseId) {
      c.strokeStyle = accent
      c.lineWidth = 2.2 / scale.value
      c.beginPath(); c.arc(n.x, n.y, r + 3.5 / scale.value, 0, Math.PI * 2); c.stroke()
    }
    if (n.id === pulseId && pulseT >= 0) {
      c.strokeStyle = accent
      c.globalAlpha = (1 - pulseT) * 0.8
      c.lineWidth = Math.max(0.4, 2.5 * (1 - pulseT)) / scale.value
      c.beginPath(); c.arc(n.x, n.y, r + 4 + pulseT * 26 / scale.value, 0, Math.PI * 2); c.stroke()
      c.globalAlpha = hl ? 1 : 0.15
    }
  }
  // 标签（LOD：≤300 才画）
  if (showLabels) {
    for (const n of visible.value) {
      const hl = !focus || hlIds.has(n.id)
      c.globalAlpha = hl ? 0.78 : 0.18
      c.fillStyle = n.ghost ? text : (n.external ? cssVar('--accent-2', '#8a5bff') : hueColor(categoryHue(n.category)))
      // 文字用固定字号（不随缩放变小）
      c.font = '10.5px system-ui, sans-serif'
      c.textBaseline = 'middle'
      c.fillText(n.name, n.x + nodeRadius(n) + 3, n.y)
    }
  }
  c.globalAlpha = 1
  c.restore()
  if (pulseT >= 0) scheduleDraw()
}
function scheduleDraw() {
  if (raf) return
  raf = requestAnimationFrame(() => { raf = 0; draw() })
}

// ── 交互（拖节点 / 平移 / 缩放 / 命中）──
let dragId: string | null = null
let panning = false
let panStart: { x: number; y: number; ox: number; oy: number } | null = null
let moved = 0
let relaxFrames = 0

function canvasPos(ev: PointerEvent | WheelEvent) {
  const cv = canvasRef.value!
  const rect = cv.getBoundingClientRect()
  return { x: ev.clientX - rect.left, y: ev.clientY - rect.top }
}

function onPointerDown(ev: PointerEvent) {
  const p = canvasPos(ev)
  const hit = hitNode(p.x, p.y)
  moved = 0
  canvasRef.value?.setPointerCapture(ev.pointerId)
  if (hit) {
    dragId = hit.id
    hoverId.value = hit.id
  } else {
    panning = true
    panStart = { x: p.x, y: p.y, ox: origin.value.x, oy: origin.value.y }
  }
}
function onPointerMove(ev: PointerEvent) {
  const p = canvasPos(ev)
  if (dragId) {
    moved++
    const n = byId.value.get(dragId)
    if (n) {
      const gp = toGraph(p.x, p.y)
      const dx = gp.x - n.x, dy = gp.y - n.y
      n.x = gp.x; n.y = gp.y
      // 局部增量：邻居跟一小部分（与 App 同参数）
      const g = graph.value
      if (g) {
        for (const e of g.edges) {
          let other: string | null = null
          if (e[0] === dragId) other = e[1]
          else if (e[1] === dragId) other = e[0]
          if (!other) continue
          const o = byId.value.get(other)
          if (!o || o.id === dragId) continue
          o.x += (gp.x - o.x) * 0.04
          o.y += (gp.y - o.y) * 0.04
        }
      }
      void dx; void dy
    }
    scheduleDraw()
    return
  }
  if (panning && panStart) {
    moved++
    origin.value = { x: panStart.ox + (p.x - panStart.x), y: panStart.oy + (p.y - panStart.y) }
    scheduleDraw()
    return
  }
  const hit = hitNode(p.x, p.y)
  const id = hit?.id ?? null
  if (id !== hoverId.value) { hoverId.value = id; scheduleDraw() }
  if (canvasRef.value) canvasRef.value.style.cursor = hit ? 'pointer' : 'grab'
}
function onPointerUp(ev: PointerEvent) {
  const p = canvasPos(ev)
  if (dragId) {
    dragId = null
    relaxFrames = 30 // 松手缓动
    relaxTick()
  } else if (panning) {
    panning = false
    panStart = null
    if (moved < 3) {
      const hit = hitNode(p.x, p.y)
      selectedId.value = hit?.id ?? null
      scheduleDraw()
    }
  }
  try { canvasRef.value?.releasePointerCapture(ev.pointerId) } catch { /* */ }
}
function relaxTick() {
  if (relaxFrames <= 0) return
  relaxFrames--
  const g = graph.value
  const vis = visible.value
  if (g && vis.length) {
    const idx = new Map(vis.map((n, i) => [n.id, i]))
    for (const n of vis) {
      let dx = 0, dy = 0
      for (const e of g.edges) {
        let other: string | null = null
        if (e[0] === n.id) other = e[1]
        else if (e[1] === n.id) other = e[0]
        if (!other) continue
        const oi = idx.get(other)
        if (oi === undefined) continue
        const o = vis[oi]
        let ddx = n.x - o.x, ddy = n.y - o.y
        let d = Math.hypot(ddx, ddy)
        if (d === 0) { ddx = 0.5; ddy = 0.5; d = 1 }
        const f = (d - K) / d * 0.06
        dx += ddx * f; dy += ddy * f
      }
      n.x -= Math.max(-12, Math.min(12, dx))
      n.y -= Math.max(-12, Math.min(12, dy))
    }
  }
  scheduleDraw()
  requestAnimationFrame(relaxTick)
}
function onWheel(ev: WheelEvent) {
  ev.preventDefault()
  const p = canvasPos(ev)
  const before = toGraph(p.x, p.y)
  const factor = Math.exp(-ev.deltaY * 0.0012)
  scale.value = Math.max(0.05, Math.min(4, scale.value * factor))
  origin.value = { x: p.x - before.x * scale.value, y: p.y - before.y * scale.value }
  scheduleDraw()
}
function locate(n: KbNode) {
  const cv = canvasRef.value
  if (!cv) return
  scale.value = Math.max(scale.value, 0.8)
  origin.value = { x: cv.clientWidth / 2 - n.x * scale.value, y: cv.clientHeight / 2 - n.y * scale.value }
  pulseId = n.id
  pulseT = 0
  selectedId.value = n.id
  const t0 = performance.now()
  const anim = (t: number) => {
    pulseT = Math.min(1, (t - t0) / 900)
    scheduleDraw()
    if (pulseT < 1) requestAnimationFrame(anim)
    else { pulseT = -1 }
  }
  requestAnimationFrame(anim)
}

function openNode(n: KbNode) {
  if (n.ghost) {
    toast(`「${n.name}」还不存在——在笔记里写 [[${n.name}]] 的地方点创建，或用 App 端图谱一键新建`, 'info')
    return
  }
  previewItem.value = { path: n.path, name: n.name, ext: n.name.split('.').pop() ?? null }
}

function openPath(p: string) {
  previewItem.value = { path: p, name: p.split('/').pop() ?? p, ext: p.split('.').pop() ?? null }
}

async function loadBacklinks() {
  const n = selected.value
  if (!n || n.ghost || !n.path) return
  blLoading.value = true
  try {
    bl.value = await api.kbBacklinks(root.value, n.path)
  } catch (e) {
    toast('反链获取失败：' + (e as Error).message, 'err')
  } finally {
    blLoading.value = false
  }
}

/** 双链跳转：解析目标 → 切换预览 */
async function onKbOpen(target: string) {
  if (!previewItem.value) return
  let nm = names.value
  if (!nm.length) {
    try { nm = await api.kbNames(root.value); names.value = nm } catch { /* */ }
  }
  const cur = previewItem.value.path
  const curDir = cur.includes('/') ? cur.slice(0, cur.lastIndexOf('/')) : ''
  const path = resolveKbTarget(nm, target, curDir)
  if (!path) { toast(`「${target}」还不存在（幽灵节点）`, 'info'); return }
  previewItem.value = { path, name: path.split('/').pop() ?? path, ext: path.split('.').pop() ?? null }
}

onMounted(() => {
  const cv = canvasRef.value
  if (cv) {
    cv.addEventListener('wheel', onWheel, { passive: false })
    ctx = cv.getContext('2d')
  }
  window.addEventListener('resize', scheduleDraw)
  load()
})
watch(selectedId, () => { bl.value = null; loadCtx() })
onBeforeUnmount(() => {
  const cv = canvasRef.value
  if (cv) cv.removeEventListener('wheel', onWheel)
  window.removeEventListener('resize', scheduleDraw)
  if (raf) cancelAnimationFrame(raf)
})
watch(root, () => load())
</script>

<template>
  <div class="kb-view">
    <div class="kb-toolbar glass">
      <button class="btn sm ghost" title="选择笔记目录" @click="showRootPicker = true">
        📁 {{ root ? root.split('/').pop() : '选择目录' }}
      </button>
      <span class="kb-stats">{{ statsLine }}</span>
      <span class="flex1" />
      <input v-model="query" class="kb-search" placeholder="搜索节点（文件名/别名）…" @input="() => { const v = visible; if (v.length) locate(v[0]) }" />
      <div class="kb-filter-wrap">
        <button class="btn sm ghost" @click="showFilter = !showFilter">
          筛选<span v-if="filterOn" class="kb-dot">•</span>
        </button>
        <div v-if="showFilter" class="kb-filter glass">
          <label><input v-model="fGhostsOnly" type="checkbox" @change="() => { fitView(); scheduleDraw() }" /> 只看幽灵（待写清单）</label>
          <label><input v-model="fHideOrphans" type="checkbox" @change="() => { fitView(); scheduleDraw() }" /> 隐藏孤岛</label>
          <label><input v-model="fHideGhosts" type="checkbox" @change="() => { fitView(); scheduleDraw() }" /> 隐藏幽灵</label>
          <template v-if="tags.length">
            <div class="kb-sec">标签</div>
            <select v-model="fTag" class="kb-select" @change="scheduleDraw">
              <option value="">全部</option>
              <option v-for="t in tags" :key="t" :value="t">#{{ t }}</option>
            </select>
          </template>
          <template v-if="categories.length">
            <div class="kb-sec">分类</div>
            <select v-model="fCategory" class="kb-select" @change="() => { fitView(); scheduleDraw() }">
              <option value="">全部分类</option>
              <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
            </select>
          </template>
          <button class="btn sm ghost" @click="() => { fCategory = ''; fTag = ''; fHideOrphans = false; fHideGhosts = false; fGhostsOnly = false; query = ''; fitView(); scheduleDraw() }">重置</button>
        </div>
      </div>
      <button class="btn sm ghost" title="适配视图" @click="fitView">⤢ 适配</button>
      <button class="btn sm ghost" title="刷新" @click="load">↻</button>
    </div>

    <div v-if="loading" class="kb-center">加载中…</div>
    <div v-else-if="error" class="kb-center">
      <p>{{ error }}</p>
      <button class="btn sm" @click="load">重试</button>
    </div>
    <div v-else-if="!root" class="kb-center">
      <p>知识库 = 任意一个装满 Markdown 笔记的目录。</p>
      <button class="btn" @click="showRootPicker = true">选择笔记目录</button>
    </div>
    <div v-else-if="!graph || !graph.stats.nodes" class="kb-center">
      <p>这个目录还没有笔记链接。</p>
      <p class="dim">在笔记里写 [[另一篇笔记的名字]] 建立第一条连接。</p>
      <button class="btn sm" @click="load">刷新</button>
    </div>
    <canvas v-else ref="canvasRef" class="kb-canvas"
            @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" />

    <div v-if="selected" class="kb-card glass">
      <div class="kb-card-head">
        <span class="kb-dot-color" :style="{ background: nodeColor(selected) }" />
        <strong>{{ selected.name }}</strong>
        <span class="flex1" />
        <button class="btn sm ghost" @click="selectedId = null">✕</button>
      </div>
      <div v-if="selected.kind === 'folder'" class="kb-meta">
        分类 · {{ selected.count }} 篇（含子分类） · {{ selected.path }}
      </div>
      <div v-else class="kb-meta">
        度 {{ selected.out + selected.ind }}（入 {{ selected.ind }} / 出 {{ selected.out }}）
        {{ selected.category ? ' · ' + selected.category : '' }}
        {{ selected.ghost ? ' · 幽灵（未创建）' : '' }}
        {{ selected.external ? ' · 库外笔记' : '' }}
      </div>
      <div v-if="selected.tags.length" class="kb-meta">
        <span v-for="t in selected.tags" :key="t" class="kb-tag">#{{ t }}</span>
      </div>
      <!-- v0.25 点按=显示内容：分类给成员清单，笔记给正文预览 -->
      <div v-if="selected.kind === 'folder'" class="kb-bl">
        <div v-if="folderChildren(selected).length" class="kb-bl">
          <button v-for="c in folderChildren(selected).slice(0, 8)" :key="c.id"
                  class="kb-bl-item" @click="selectedId = c.id">
            <strong>{{ c.name }}</strong>
            <span v-if="c.summary" class="kb-bl-ex">{{ c.summary.slice(0, 60) }}</span>
          </button>
          <div v-if="folderChildren(selected).length > 8" class="kb-meta">
            …共 {{ selected.count }} 篇
          </div>
        </div>
        <div v-else class="kb-meta">空分类——还没有笔记。</div>
      </div>
      <p v-else-if="ctxLoading" class="kb-meta">加载内容…</p>
      <p v-else-if="ctxExcerpt" class="kb-summary">{{ ctxExcerpt }}</p>
      <p v-else-if="selected.summary" class="kb-summary">{{ selected.summary }}</p>
      <div class="kb-actions">
        <button v-if="selected.kind !== 'folder' && !selected.ghost && !bl"
                class="btn sm ghost" :disabled="blLoading" @click="loadBacklinks">
          {{ blLoading ? '加载中…' : '反链' }}
        </button>
        <button v-if="selected.kind === 'folder'" class="btn sm" @click="pickFolder(selected)">
          只看该分类
        </button>
        <button v-if="!selected.ghost && selected.path" class="btn sm" @click="openNode(selected)">打开</button>
      </div>
      <template v-if="bl">
        <div v-if="bl.backlinks.length" class="kb-bl">
          <div class="kb-sec">谁链到了这篇</div>
          <button v-for="b in bl.backlinks" :key="b.path" class="kb-bl-item"
                  @click="openPath(b.path)">
            <strong>{{ b.name }}</strong>
            <span v-if="b.excerpt" class="kb-bl-ex">{{ b.excerpt }}</span>
          </button>
        </div>
        <div v-else class="kb-meta">还没有反链。</div>
      </template>
    </div>

    <Modal v-if="showRootPicker" title="选择笔记目录（图谱根）" @close="showRootPicker = false">
      <DirPicker @pick="(p: string) => { root = p; showRootPicker = false }" @close="showRootPicker = false" />
    </Modal>
    <FileViewer v-if="previewItem" :items="[previewItem]" :model-value="0" :me="props.me"
                @close="previewItem = null" @kb-open="onKbOpen" />
  </div>
</template>

<style scoped>
.kb-view { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 0; }
.kb-toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; margin: 10px 12px 0; border-radius: var(--radius-sm); flex-wrap: wrap; }
.kb-stats { font-size: 12px; color: var(--text-2); }
.kb-search { width: 200px; max-width: 40vw; padding: 5px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--glass-strong); color: var(--text); font-size: 12.5px; }
.kb-filter-wrap { position: relative; }
.kb-filter { position: absolute; right: 0; top: 36px; z-index: 30; display: flex; flex-direction: column; gap: 6px; padding: 12px; border-radius: var(--radius-sm); min-width: 220px; box-shadow: var(--shadow); }
.kb-filter label { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text); cursor: pointer; }
.kb-sec { font-size: 11px; font-weight: 700; color: var(--text-3); margin-top: 2px; }
.kb-select { border: 1px solid var(--line); border-radius: 8px; background: var(--glass-strong); color: var(--text); padding: 4px 8px; font-size: 12.5px; }
.kb-dot { color: var(--accent); margin-left: 3px; }
.kb-canvas { flex: 1; width: 100%; min-height: 0; cursor: grab; touch-action: none; }
.kb-center { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--text-2); font-size: 13.5px; }
.kb-center .dim { font-size: 12px; color: var(--text-3); }
.kb-card { position: absolute; left: 20px; bottom: 20px; max-width: 380px; padding: 12px 14px; border-radius: var(--radius-sm); box-shadow: var(--shadow); z-index: 20; }
.kb-card-head { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.kb-dot-color { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.kb-meta { font-size: 11.5px; color: var(--text-2); margin-top: 4px; }
.kb-tag { color: var(--accent-2); margin-right: 6px; font-size: 11px; }
.kb-summary { font-size: 12px; color: var(--text-2); margin: 6px 0 0; max-height: 60px; overflow: hidden; }
.kb-actions { display: flex; justify-content: flex-end; margin-top: 8px; }
.kb-bl { margin-top: 6px; }
.kb-bl-item { display: block; width: 100%; text-align: left; background: none; border: none; border-top: 1px solid var(--line); padding: 6px 0; cursor: pointer; color: var(--text); font-size: 12.5px; }
.kb-bl-item strong { font-size: 12.5px; }
.kb-bl-ex { display: block; color: var(--text-2); font-size: 11.5px; margin-top: 2px; }
.flex1 { flex: 1; }
@media (max-width: 640px) {
  .kb-search { width: 130px; }
  .kb-card { left: 10px; right: 10px; bottom: 10px; max-width: none; }
}
</style>
