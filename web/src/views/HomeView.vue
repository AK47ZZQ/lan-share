<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, uploadResumable, type Entry } from '../api'
import { toast } from '../ui'
import Modal from '../components/Modal.vue'
import DirPicker from '../components/DirPicker.vue'
import ShareModal from '../components/ShareModal.vue'
import ContextMenu from '../components/ContextMenu.vue'

defineProps<{ me: { username: string; role: string } }>()

const grid = ref(localStorage.getItem('lsView') !== 'list')
watch(grid, g => localStorage.setItem('lsView', g ? 'grid' : 'list'))
function hashName(s: string): number {
  let h = 0
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return h
}

const path = ref('')
const items = ref<Entry[]>([])
const err = ref('')
const searchQ = ref('')
const searchResults = ref<Entry[] | null>(null)
const view = ref<'browse' | 'recent'>('browse')
const recentItems = ref<Entry[]>([])

// 多选
const selectMode = ref(false)
const selected = ref<Set<string>>(new Set())
const allSelected = computed(() => items.value.length > 0 && items.value.every(i => selected.value.has(i.path)))

// 操作菜单（⋯ 按钮与右键共用）
const menu = ref<{ x: number; y: number; e: Entry } | null>(null)
function openMenu(ev: MouseEvent, e: Entry) {
  menu.value = { x: ev.clientX, y: ev.clientY, e }
}
function menuAct(action: string) {
  const e = menu.value!.e
  menu.value = null
  if (action === 'open') open(e)
  else if (action === 'download') download(e)
  else if (action === 'share') sharePaths.value = [e.path]
  else if (action === 'rename') { renameTarget.value = e; renameName.value = e.name }
  else if (action === 'move') { selectMode.value = true; selected.value = new Set([e.path]); showMove.value = true }
  else if (action === 'delete') { selectMode.value = true; selected.value = new Set([e.path]); void doDelete() }
  else if (action === 'multi') { selectMode.value = true; selected.value = new Set([e.path]) }
}

// 弹窗
const showMove = ref(false)
const sharePaths = ref<string[] | null>(null)
const renameTarget = ref<Entry | null>(null)
const renameName = ref('')

// 上传队列
const uploads = ref<{ name: string; sent: number; total: number; stage: string }[]>([])
const uploading = ref(false)

const crumbs = computed(() => {
  const parts = path.value.split('/').filter(Boolean)
  const out = [{ name: '🏠', path: '' }]
  parts.forEach((p, i) => out.push({ name: p, path: parts.slice(0, i + 1).join('/') }))
  return out
})

function thumbOf(e: Entry): string | null {
  if (e.is_dir) return null
  const ext = (e.ext || '').toLowerCase()
  if (['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'].includes(ext)) return `/thumb/${encodeURI(e.path)}`
  if (['.mp4', '.mkv', '.mov', '.webm', '.avi'].includes(ext)) return `/thumb/${encodeURI(e.path)}`
  return null
}
function fileIcon(e: Entry): string {
  if (e.is_dir) return '📁'
  const ext = (e.ext || '').toLowerCase()
  if (['.mp4', '.mkv', '.mov', '.webm', '.avi'].includes(ext)) return '🎬'
  if (['.mp3', '.flac', '.wav', '.m4a'].includes(ext)) return '🎵'
  if (['.zip', '.7z', '.rar'].includes(ext)) return '🗜️'
  if (['.pdf'].includes(ext)) return '📕'
  if (['.doc', '.docx'].includes(ext)) return '📘'
  if (['.xls', '.xlsx'].includes(ext)) return '📗'
  if (['.txt', '.md'].includes(ext)) return '📄'
  return '📄'
}

async function load(p: string) {
  err.value = ''
  searchResults.value = null
  view.value = 'browse'
  try {
    const r = await api.list(p)
    path.value = r.path
    items.value = r.items
    selected.value.clear()
    selectMode.value = false
  } catch (e) { err.value = (e as Error).message }
}

function open(e: Entry) {
  if (selectMode.value) { toggle(e.path); return }
  if (e.is_dir) void load(e.path)
  else previewFile.value = e
}

// ── 选择 ──
function toggle(p: string) {
  if (selected.value.has(p)) selected.value.delete(p)
  else selected.value.add(p)
  if (!selected.value.size) selectMode.value = false
  selected.value = new Set(selected.value)
}
function toggleAll() {
  if (allSelected.value) { selected.value.clear(); selectMode.value = false }
  else selected.value = new Set(items.value.map(i => i.path))
}
function startSelect(e: Entry) {
  selectMode.value = true
  selected.value = new Set([e.path])
}

// ── 操作 ──
async function doDelete() {
  const paths = [...selected.value]
  if (!confirm(`删除选中的 ${paths.length} 项？（可从回收站恢复）`)) return
  try {
    for (const p of paths) await api.remove(p)
    toast(`已删除 ${paths.length} 项，可在回收站恢复`, 'ok')
    await load(path.value)
  } catch (e) { toast((e as Error).message, 'err') }
}
async function doMove(dest: string) {
  showMove.value = false
  try {
    const r = await api.move([...selected.value], dest)
    const fails = r.results.filter(x => !x.ok)
    if (fails.length) toast(`移动完成，${fails.length} 项失败：${fails[0].error}`, 'err')
    else toast(`已移动 ${r.results.length} 项`, 'ok')
    await load(path.value)
  } catch (e) { toast((e as Error).message, 'err') }
}
async function doRename() {
  if (!renameTarget.value) return
  try {
    await api.rename(renameTarget.value.path, renameName.value)
    toast('已重命名', 'ok')
    renameTarget.value = null
    await load(path.value)
  } catch (e) { toast((e as Error).message, 'err') }
}
function doShare() {
  sharePaths.value = [...selected.value]
}
async function doZip() {
  try {
    const r = await api.zip([...selected.value])
    window.open(r.url, '_blank')
  } catch (e) { toast((e as Error).message, 'err') }
}
function download(e: Entry) { window.open(`/files/${encodeURI(e.path)}`, '_blank') }

// ── 上传 ──
async function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (!input.files?.length) return
  uploading.value = true
  for (const f of Array.from(input.files)) {
    const item = { name: f.name, sent: 0, total: f.size, stage: '排队' }
    uploads.value.push(item)
    try {
      await uploadResumable(path.value, f, (sent, total, stage) => {
        item.sent = sent; item.total = total; item.stage = stage
      })
      item.stage = f.size >= 5 * 1024 * 1024 ? '完成' : '完成'
    } catch (e) {
      item.stage = '失败：' + (e as Error).message
      toast(`${f.name} 上传失败`, 'err')
    }
  }
  uploading.value = false
  input.value = ''
  setTimeout(() => { uploads.value = uploads.value.filter(u => !u.stage.startsWith('完成')) }, 2500)
  await load(path.value)
}

async function newFolder() {
  const name = prompt('文件夹名称')
  if (!name) return
  try {
    await api.mkdir([path.value, name].filter(Boolean).join('/'))
    await load(path.value)
  } catch (e) { toast((e as Error).message, 'err') }
}

async function runSearch() {
  const q = searchQ.value.trim()
  if (!q) { searchResults.value = null; return }
  try {
    searchResults.value = await api.search(q)
    view.value = 'browse'
  } catch (e) { toast((e as Error).message, 'err') }
}
async function showRecent() {
  try {
    recentItems.value = await api.recent(40)
    searchResults.value = null
    view.value = view.value === 'recent' ? 'browse' : 'recent'
  } catch (e) { toast((e as Error).message, 'err') }
}

const searchMode = computed(() => view.value === 'recent' || !!searchResults.value)

// 页内预览（不再 window.open——弹窗拦截器会吞掉新页）
const previewFile = ref<Entry | null>(null)
const IMG = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
const VID = ['.mp4', '.mkv', '.mov', '.webm', '.avi']
const AUD = ['.mp3', '.flac', '.wav', '.m4a', '.ogg']
const previewKind = computed(() => {
  const e = previewFile.value
  if (!e) return null
  const ext = (e.ext || '').toLowerCase()
  if (IMG.includes(ext)) return 'image'
  if (VID.includes(ext)) return 'video'
  if (AUD.includes(ext)) return 'audio'
  if (ext === '.pdf' || ['.txt', '.md', '.log', '.json'].includes(ext)) return 'frame'
  return 'download'
})
const previewUrl = computed(() => previewFile.value ? `/preview/${encodeURI(previewFile.value.path)}` : '')

const shownItems = computed(() => {
  if (view.value === 'recent') return recentItems.value
  if (searchResults.value) return searchResults.value
  return items.value
})

onMounted(() => load(''))
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>文件</h1>
      <button v-if="path && view === 'browse'" class="btn ghost sm" @click="load(path.split('/').slice(0, -1).join('/'))">← 返回</button>
      <input v-model="searchQ" placeholder="🔍 搜索文件…" class="searchbox"
             @keydown.enter="runSearch" @input="!searchQ && (searchResults = null)" />
      <button class="btn ghost sm" @click="showRecent">{{ view === 'recent' ? '返回浏览' : '🕐 最近' }}</button>
      <button class="btn sm" @click="newFolder">＋ 文件夹</button>
      <label class="btn sm" style="position:relative">
        {{ uploading ? '上传中…' : '⬆ 上传' }}
        <input type="file" multiple hidden @change="onPick" />
      </label>
      <button class="btn ghost sm" @click="selectMode = !selectMode; if (!selectMode) selected.clear()">
        {{ selectMode ? '取消选择' : '☑ 选择' }}
      </button>
      <span class="view-toggle">
        <button :class="{ on: grid }" title="网格视图" @click="grid = true">▦</button>
        <button :class="{ on: !grid }" title="列表视图" @click="grid = false">☰</button>
      </span>
    </div>

    <div class="crumbs" v-if="searchMode">
      <span class="cur">{{ view === 'recent' ? '最近文件' : `搜索“${searchQ}”` }}</span>
      <a href="#" style="margin-left:10px" @click.prevent="searchQ = ''; searchResults = null; view = 'browse'">返回浏览</a>
    </div>

    <div class="crumbs" v-if="!searchMode">
      <template v-for="(c, i) in crumbs" :key="c.path">
        <span v-if="i" class="sep">/</span>
        <a href="#" :class="{ cur: i === crumbs.length - 1 }" @click.prevent="load(c.path)">{{ c.name }}</a>
      </template>
    </div>

    <p v-if="err" class="err">{{ err }}</p>

    <div v-if="!shownItems.length" class="card empty-state">
      <div class="art">{{ view === 'browse' ? '🗂️' : '🔎' }}</div>
      <div>{{ view === 'browse' ? '此目录为空' : '没有匹配结果' }}</div>
      <div class="hint" v-if="view === 'browse'">点右上「⬆ 上传」添加文件，或拖拽文件夹卡片进入</div>
    </div>
    <div v-if="shownItems.length && grid && view === 'browse'" class="fgrid">
      <div v-for="(e, gi) in shownItems" :key="e.path"
           class="gcard" :class="{ selected: selected.has(e.path) }"
           @click="open(e)" @contextmenu.prevent="openMenu($event, e)">
        <span v-if="selectMode" class="checkbox gcheck" :class="{ on: selected.has(e.path) }" @click.stop="toggle(e.path)">✓</span>
        <span class="ops" @click.stop>
          <button class="iconbtn" title="分享" @click="sharePaths = [e.path]">🔗</button>
          <button class="iconbtn" title="更多操作" @click.stop="openMenu($event, e)">⋯</button>
        </span>
        <span class="gicon" :class="e.is_dir ? 'dir' + (hashName(e.name) % 4) : ''">
          <img v-if="thumbOf(e)" :src="thumbOf(e)!" loading="lazy" alt="" />
          <template v-else>{{ fileIcon(e) }}</template>
        </span>
        <span class="gname" :title="e.name">{{ e.name }}</span>
        <span class="gsub">{{ e.is_dir ? e.modified.slice(5, 10) : e.size_human }}</span>
      </div>
    </div>
    <div v-else-if="shownItems.length" class="card flist" style="padding:8px">
      <div v-for="e in shownItems" :key="e.path"
           class="frow" :class="{ selected: selected.has(e.path) }"
           @click="open(e)" @contextmenu.prevent="openMenu($event, e)">
        <span v-if="selectMode" class="checkbox" :class="{ on: selected.has(e.path) }" @click.stop="toggle(e.path)">✓</span>
        <span class="icon">
          <img v-if="thumbOf(e)" :src="thumbOf(e)!" loading="lazy" alt="" />
          <template v-else>{{ fileIcon(e) }}</template>
        </span>
        <span class="name">
          <div>{{ e.name }}</div>
          <small>{{ e.modified.slice(0, 16).replace('T', ' ') }}</small>
        </span>
        <span class="meta">{{ e.is_dir ? '' : e.size_human }}</span>
        <span class="ops" @click.stop>
          <button class="iconbtn" title="下载" @click="download(e)" v-if="!e.is_dir">⬇</button>
          <button class="iconbtn" title="分享" @click="sharePaths = [e.path]">🔗</button>
          <button class="iconbtn" title="重命名" @click="renameTarget = e; renameName = e.name">✏️</button>
          <button class="iconbtn" title="更多操作" @click.stop="openMenu($event, e)">⋯</button>
        </span>
      </div>
    </div>

    <!-- 多选操作条 -->
    <div v-if="selectMode && selected.size" class="opsbar">
      <span class="count">已选 {{ selected.size }}</span>
      <button class="btn ghost sm" @click="toggleAll">{{ allSelected ? '全不选' : '全选' }}</button>
      <button class="btn ghost sm" @click="doZip">打包下载</button>
      <button class="btn ghost sm" @click="showMove = true">移动到…</button>
      <button v-if="selected.size === 1" class="btn ghost sm" @click="doShare">分享</button>
      <button class="btn danger sm" @click="doDelete">删除</button>
      <button class="iconbtn" title="退出多选" @click="selectMode = false; selected.clear()">✕</button>
    </div>

    <!-- 上传队列 -->
    <div v-if="uploads.length" class="upl card" style="padding:12px 16px">
      <div style="font-size:13px;font-weight:600;margin-bottom:4px">上传队列</div>
      <div v-for="u in uploads" :key="u.name" class="item">
        <div style="display:flex;justify-content:space-between;gap:10px">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ u.name }}</span>
          <span style="color:var(--text-3)">{{ u.stage }}</span>
        </div>
        <div class="bar"><i :style="{ width: (u.total ? Math.round(u.sent / u.total * 100) : 0) + '%' }" /></div>
      </div>
    </div>

    <Modal v-if="showMove" title="移动到…" wide @close="showMove = false">
      <DirPicker @pick="doMove" @close="showMove = false" />
    </Modal>
    <Modal v-if="renameTarget" title="重命名" @close="renameTarget = null">
      <input v-model="renameName" style="width:100%" @keydown.enter="doRename" />
      <div class="actions">
        <button class="btn ghost sm" @click="renameTarget = null">取消</button>
        <button class="btn sm" @click="doRename">确定</button>
      </div>
    </Modal>
    <Modal v-if="sharePaths" title="创建分享" @close="sharePaths = null">
      <ShareModal :paths="sharePaths" @close="sharePaths = null" />
    </Modal>

    <!-- 操作菜单（⋯ / 右键） -->
    <ContextMenu v-if="menu" :x="menu.x" :y="menu.y" @close="menu = null">
      <button class="mi" @click="menuAct('open')">📂 {{ menu.e.is_dir ? '打开' : '预览' }}</button>
      <button v-if="!menu.e.is_dir" class="mi" @click="menuAct('download')">⬇ 下载</button>
      <button class="mi" @click="menuAct('share')">🔗 分享</button>
      <button class="mi" @click="menuAct('rename')">✏️ 重命名</button>
      <button class="mi" @click="menuAct('move')">📂 移动到…</button>
      <div class="divider" />
      <button class="mi danger" @click="menuAct('delete')">🗑️ 删除</button>
      <button class="mi" @click="menuAct('multi')">☑ 多选模式</button>
    </ContextMenu>

    <!-- 页内预览灯箱 -->
    <teleport to="body">
      <div v-if="previewFile" class="overlay" @click.self="previewFile = null">
        <div class="preview-box">
          <div class="preview-head">
            <span class="preview-name" :title="previewFile.name">{{ previewFile.name }}</span>
            <span style="display:flex;gap:6px;align-items:center">
              <a class="btn ghost sm" :href="`/files/${encodeURI(previewFile.path)}`" target="_blank">⬇ 下载</a>
              <button class="iconbtn" style="color:#fff" @click="previewFile = null">✕</button>
            </span>
          </div>
          <img v-if="previewKind === 'image'" :src="previewUrl" class="preview-media" />
          <video v-else-if="previewKind === 'video'" :src="previewUrl" class="preview-media" controls autoplay />
          <audio v-else-if="previewKind === 'audio'" :src="previewUrl" controls autoplay style="width:min(560px,90vw)" />
          <iframe v-else-if="previewKind === 'frame'" :src="previewUrl" class="preview-frame" />
          <div v-else class="card" style="padding:26px;text-align:center">
            <p>该类型暂不支持在线预览</p>
            <a class="btn sm" :href="`/files/${encodeURI(previewFile.path)}`" target="_blank">⬇ 下载文件</a>
          </div>
        </div>
      </div>
    </teleport>
  </main>
</template>
