<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Entry, type Me } from '../api'
import { confirmDlg, promptDlg, toast } from '../ui'
import { uploadFiles, uploadingNow } from '../uploader'
import Modal from '../components/Modal.vue'
import DirPicker from '../components/DirPicker.vue'
import ShareModal from '../components/ShareModal.vue'
import ContextMenu from '../components/ContextMenu.vue'
import FileViewer from '../components/FileViewer.vue'
import UploadQueue from '../components/UploadQueue.vue'

defineProps<{ me: Me }>()

const route = useRoute()

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

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast('已复制', 'ok')
  } catch {
    await promptDlg('请手动复制：', text)
  }
}

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
const showQr = ref(false)
const upUrl = location.origin + '/up'
const publicBase = ref('')
function loadPublic() { api.getPublicUrl().then(r => publicBase.value = r.public_base_url).catch(() => {}) }
const sharePaths = ref<string[] | null>(null)
const renameTarget = ref<Entry | null>(null)
const renameName = ref('')

// 上传队列（共享引擎见 uploader.ts）

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
  else openViewer(e)
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
  if (!(await confirmDlg(`删除选中的 ${paths.length} 项？（可从回收站恢复）`, { danger: true, okText: '删除' }))) return
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

// ── 上传（队列引擎在 uploader.ts；粘贴/拖拽入口在此）──
const uploading = uploadingNow

async function startUpload(files: File[]) {
  if (!files.length) return
  await uploadFiles(files, {
    destDir: path.value,
    existsNames: items.value.map(e => e.name),
    onEachFail: n => toast(`${n} 上传失败`, 'err'),
  })
  await load(path.value)
}

async function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (!input.files?.length) return
  await startUpload(Array.from(input.files))
  input.value = ''
}

// 粘贴上传：Ctrl+V 文件/截图（截图自动命名），输入框聚焦时不抢
function stampName(name: string): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `截图-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}${name.match(/(\.\w+)$/)?.[1] ?? '.png'}`
}
function onPaste(ev: ClipboardEvent) {
  const t = ev.target as HTMLElement | null
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
  const files = Array.from(ev.clipboardData?.files ?? [])
  if (!files.length) return
  ev.preventDefault()
  startUpload(files.map(f => /^image(\.\w+)?$/i.test(f.name) && f.type.startsWith('image/')
    ? new File([f], stampName(f.name), { type: f.type }) : f))
}

// 全屏拖拽上传覆盖层
const dragDepth = ref(0)
function hasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes('Files')
}
function onDragEnter(e: DragEvent) { if (hasFiles(e)) { e.preventDefault(); dragDepth.value++ } }
function onDragOver(e: DragEvent) { if (hasFiles(e)) e.preventDefault() }
function onDragLeave() { dragDepth.value = Math.max(0, dragDepth.value - 1) }
function onDrop(e: DragEvent) {
  e.preventDefault()
  dragDepth.value = 0
  const files = Array.from(e.dataTransfer?.files ?? [])
  if (files.length) startUpload(files)
}

async function newFolder() {
  const name = await promptDlg('文件夹名称', '', { title: '新建文件夹' })
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

// 页内预览（统一 FileViewer：相邻导航/缩放/信息面板/文本渲染）
const previewIndex = ref(-1)
const navItems = computed(() => shownItems.value.filter(i => !i.is_dir))
function openViewer(e: Entry) {
  const idx = navItems.value.findIndex(x => x.path === e.path)
  if (idx >= 0) previewIndex.value = idx
}
function onViewerShare(item: { path: string }) {
  previewIndex.value = -1
  sharePaths.value = [item.path]
}

const shownItems = computed(() => {
  if (view.value === 'recent') return recentItems.value
  if (searchResults.value) return searchResults.value
  return items.value
})

onMounted(() => {
  load(String(route.query.dir || ''))
  loadPublic()
  window.addEventListener('paste', onPaste)
  window.addEventListener('dragenter', onDragEnter)
  window.addEventListener('dragover', onDragOver)
  window.addEventListener('dragleave', onDragLeave)
  window.addEventListener('drop', onDrop)
})
onUnmounted(() => {
  window.removeEventListener('paste', onPaste)
  window.removeEventListener('dragenter', onDragEnter)
  window.removeEventListener('dragover', onDragOver)
  window.removeEventListener('dragleave', onDragLeave)
  window.removeEventListener('drop', onDrop)
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <div class="title-row"><h1>文件</h1></div>
      <button v-if="path && view === 'browse'" class="btn ghost sm" @click="load(path.split('/').slice(0, -1).join('/'))">← 返回</button>
      <input v-model="searchQ" placeholder="搜索文件…" class="searchbox"
             @keydown.enter="runSearch" @input="!searchQ && (searchResults = null)" />
      <button class="btn ghost sm" @click="runSearch" title="搜索">🔍</button>
      <button class="btn ghost sm" @click="showRecent">{{ view === 'recent' ? '返回浏览' : '🕐 最近' }}</button>
      <button class="btn sm" @click="newFolder">＋ 文件夹</button>
      <button class="btn ghost sm" @click="showQr = true">📲 手机上传</button>
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

    <div class="crumbs" v-if="!searchMode && crumbs.length > 1">
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
        <span class="gicon" :class="e.is_private_root ? 'private' : e.is_dir ? 'dir' + (hashName(e.name) % 4) : ''">
          <img v-if="thumbOf(e)" :src="thumbOf(e)!" loading="lazy" alt="" />
          <template v-else>{{ e.is_private_root ? '🔒' : fileIcon(e) }}</template>
        </span>
        <span class="gname" :title="e.name">{{ e.name }}</span>
        <span class="gsub">{{ e.is_dir ? e.modified.slice(5, 10) : e.size_human }}</span>
        <span class="ops gfoot" @click.stop>
          <button class="iconbtn" title="分享" @click="sharePaths = [e.path]">🔗</button>
          <button class="iconbtn" title="更多操作" @click.stop="openMenu($event, e)">⋯</button>
        </span>
      </div>
    </div>
    <div v-else-if="shownItems.length" class="card flist" style="padding:8px">
      <div v-for="e in shownItems" :key="e.path"
           class="frow file-row" :class="{ selected: selected.has(e.path) }"
           @click="open(e)" @contextmenu.prevent="openMenu($event, e)">
        <span v-if="selectMode" class="checkbox" :class="{ on: selected.has(e.path) }" @click.stop="toggle(e.path)">✓</span>
        <span class="icon" :class="{ 'icon-private': e.is_private_root }">
          <img v-if="thumbOf(e)" :src="thumbOf(e)!" loading="lazy" alt="" />
          <template v-else>{{ e.is_private_root ? '🔒' : fileIcon(e) }}</template>
        </span>
        <span class="name">
          <div>{{ e.name }}</div>
          <small>{{ e.modified.slice(5, 16).replace('T', ' ') }}</small>
        </span>
        <span class="meta">{{ e.is_dir ? '目录' : e.size_human }}</span>
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

    <!-- 上传队列（共享组件） -->
    <UploadQueue :dest-dir="path" />

    <!-- 拖拽上传覆盖层 -->
    <teleport to="body">
      <div v-if="dragDepth" class="drop-overlay">
        <div class="drop-hint">
          <div style="font-size:44px">📥</div>
          <div style="font-size:16px;font-weight:600">松开上传到当前目录</div>
          <div style="font-size:13px;color:var(--text-3)">/{{ path }}</div>
        </div>
      </div>
    </teleport>

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

    <!-- 手机上传二维码（双地址） -->
    <Modal v-if="showQr" title="📲 手机扫码上传" @close="showQr = false">
      <div style="display:grid;gap:14px">
        <div style="display:flex;gap:14px;justify-content:center;flex-wrap:wrap">
          <div style="text-align:center;display:grid;gap:6px;justify-items:center">
            <img :src="`/api/qr?text=${encodeURIComponent(upUrl)}`"
                 style="width:160px;height:160px;background:#fff;padding:8px;border-radius:12px" />
            <b style="font-size:13px">🏠 局域网</b>
            <span style="font-size:11.5px;color:var(--text-3)">手机连家里 WiFi 时扫</span>
            <button class="btn ghost sm" @click="copyText(upUrl)">📋 复制</button>
          </div>
          <div v-if="publicBase" style="text-align:center;display:grid;gap:6px;justify-items:center">
            <img :src="`/api/qr?text=${encodeURIComponent(publicBase + '/up')}`"
                 style="width:160px;height:160px;background:#fff;padding:8px;border-radius:12px" />
            <b style="font-size:13px">🌐 公网 (FRP)</b>
            <span style="font-size:11.5px;color:var(--text-3)">在外用 5G 时扫（较慢）</span>
            <button class="btn ghost sm" @click="copyText(publicBase + '/up')">📋 复制</button>
          </div>
        </div>
        <p style="font-size:12.5px;color:var(--text-3);margin:0;text-align:center">
          照片/文件将备份到你的 <b>incoming</b> 目录
          <template v-if="!publicBase"> · 未配置公网地址（管理面板可设）</template>
        </p>
      </div>
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

    <!-- 页内预览（统一 FileViewer） -->
    <FileViewer v-if="previewIndex >= 0 && navItems.length" :items="navItems"
                v-model="previewIndex" :me="me" @close="previewIndex = -1"
                @share="onViewerShare" @refresh="load(path)" />
  </main>
</template>
