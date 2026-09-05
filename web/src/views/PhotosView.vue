<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { api, type PhotoItem, type PhotoFacets, type Album, type Me } from '../api'
import { confirmDlg, promptDlg, toast } from '../ui'
import Modal from '../components/Modal.vue'
import ShareModal from '../components/ShareModal.vue'
import FileViewer, { type ViewerItem } from '../components/FileViewer.vue'

const props = defineProps<{ me: Me }>()

interface Ph extends PhotoItem {}
const groups = ref<{ date: string; photos: Ph[] }[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const err = ref('')
const viewerIndex = ref(-1)
const LIMIT = 120

// 预览器数据：已加载照片扁平化 + 预览内分享
const navItems = computed<ViewerItem[]>(() =>
  groups.value.flatMap(g => g.photos.map(p => ({
    path: p.path, name: p.name, ext: p.path.match(/(\.[^.]+)$/)?.[1] ?? null,
    taken: p.taken, uploader: p.uploader, modified: p.taken,
  }))))
const sharePaths = ref<string[] | null>(null)
function onViewerShare(item: ViewerItem) {
  viewerIndex.value = -1
  sharePaths.value = [item.path]
}

// 双维度筛选 + 精选专辑
const facets = ref<PhotoFacets | null>(null)
const albums = ref<Album[]>([])
const selMonth = ref('')
const selUploader = ref('')
const selAlbum = ref('')   // v0.14 文件夹式精选专辑 id（非空时按专辑条目过滤）
const filterDesc = computed(() => {
  const parts: string[] = []
  if (selAlbum.value) parts.push(`专辑：${albums.value.find(a => a.id === selAlbum.value)?.title ?? selAlbum.value}`)
  if (selMonth.value) parts.push(selMonth.value)
  if (selUploader.value) parts.push(selUploader.value)
  return parts.join(' · ')
})

async function loadFacets() {
  try {
    facets.value = await api.photoFacets()
    albums.value = await api.albums()
  } catch { /* 维度加载失败不影响时间线 */ }
}

async function loadMore() {
  if (loading.value) return
  loading.value = true
  err.value = ''
  try {
    const r = await api.photos(LIMIT, offset.value, selMonth.value || undefined, selUploader.value || undefined, selAlbum.value || undefined)
    total.value = r.total
    for (const p of r.items) {
      const last = groups.value[groups.value.length - 1]
      if (last && last.date === p.taken) last.photos.push(p)
      else groups.value.push({ date: p.taken, photos: [p] })
    }
    offset.value += r.items.length
  } catch (e) { err.value = (e as Error).message }
  finally { loading.value = false }
}

function applyFilter() {
  groups.value = []
  offset.value = 0
  total.value = 0
  void loadMore()
}
function pickMonth(m: string) {
  selMonth.value = selMonth.value === m ? '' : m
  applyFilter()
}
function pickUploader(u: string) {
  selUploader.value = selUploader.value === u ? '' : u
  applyFilter()
}
function isItemAlbum(a: Album) {
  // v0.14 判定：条目专辑（勾选过内容或无月份）；服务端对旧月份书签型
  // 也回 count=0，不能拿 count!=null 当判据（审查 P1，否则 web 上建的
  // 月份书签专辑点了必是空列表）
  return (a.count ?? 0) > 0 || !a.month
}
function pickAlbum(a: Album) {
  if (isItemAlbum(a)) {
    // v0.14 条目专辑：再次点击取消
    selAlbum.value = selAlbum.value === a.id ? '' : a.id
    selMonth.value = ''
  } else {
    // 旧月份书签型专辑
    selAlbum.value = ''
    selMonth.value = selMonth.value === a.month ? '' : (a.month ?? '')
  }
  applyFilter()
}

async function featureMonth() {
  if (!selMonth.value) return
  const title = await promptDlg(`给 ${selMonth.value} 的精选专辑起个名字：`, selMonth.value, { title: '设为精选专辑' })
  if (!title) return
  try {
    await api.addAlbum(title.trim(), selMonth.value)
    albums.value = await api.albums()
    toast('已设为精选专辑', 'ok')
  } catch (e) { toast((e as Error).message, 'err') }
}
async function removeAlbum(a: Album) {
  // 二次确认（R1-P2-7）：✕ 热区小，手滑即删
  if (!(await confirmDlg(`移除精选专辑「${a.title}」？（不影响照片本身）`, { danger: true, okText: '移除' }))) return
  try {
    await api.deleteAlbum(a.id)
    albums.value = albums.value.filter(x => x.id !== a.id)
    toast('已移除精选专辑', 'ok')
  } catch (e) { toast((e as Error).message, 'err') }
}

function onScroll() {
  if (loading.value || (total.value && offset.value >= total.value)) return
  if (window.innerHeight + window.scrollY > document.body.scrollHeight - 500) void loadMore()
}

onMounted(() => { void loadMore(); void loadFacets(); window.addEventListener('scroll', onScroll) })
onUnmounted(() => window.removeEventListener('scroll', onScroll))   // 不移除会跨路由泄漏：残监听带着旧 refs 持续触发加载
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>照片</h1>
      <span class="badge" v-if="total">{{ total }} 张<template v-if="filterDesc"> · {{ filterDesc }}</template></span>
      <button v-if="selMonth" class="btn ghost sm" style="margin-left:auto"
              title="把当前月份保存为精选专辑（家庭共创，谁都能建）" @click="featureMonth">⭐ 本月设为精选</button>
    </div>

    <!-- 精选专辑 -->
    <div v-if="albums.length" class="album-row">
      <div v-for="a in albums" :key="a.id" class="album-card" :class="{ on: selAlbum === a.id || selMonth === a.month }"
           @click="pickAlbum(a)">
        <span class="album-title">⭐ {{ a.title }}</span>
        <span class="album-sub">{{ isItemAlbum(a) ? `${a.count ?? 0} 项` : a.month }}</span>
        <button v-if="me.role === 'admin' || a.created_by === me.username" class="album-del" :title="a.created_by ? `${a.created_by} 创建` : '移除专辑'"
                @click.stop="removeAlbum(a)">✕</button>
      </div>
    </div>

    <!-- 维度筛选（月份 / 拍摄者） -->
    <div v-if="facets && (facets.months.length > 1 || facets.uploaders.length > 1)" class="facet-row">
      <template v-if="facets.months.length > 1">
        <button v-for="m in facets.months.slice(0, 12)" :key="m.month" class="chip"
                :class="{ on: selMonth === m.month }" @click="pickMonth(m.month)">
          {{ m.month }} <small>{{ m.count }}</small>
        </button>
      </template>
      <span class="facet-sep" />
      <template v-if="facets.uploaders.length > 1">
        <button v-for="u in facets.uploaders" :key="u.name" class="chip"
                :class="{ on: selUploader === u.name }" @click="pickUploader(u.name)">
          👤{{ u.name }} <small>{{ u.count }}</small>
        </button>
      </template>
      <button v-if="selMonth || selUploader || selAlbum" class="chip clear" @click="selMonth = ''; selUploader = ''; selAlbum = ''; applyFilter()">✕ 清除筛选</button>
    </div>

    <p v-if="err" class="err">{{ err }}</p>
    <div v-if="!groups.length && !loading" class="card empty">
      {{ filterDesc ? '该维度下还没有照片' : '还没有照片（自动备份的手机照片会出现在这里）' }}
    </div>
    <div v-for="g in groups" :key="g.date">
      <div class="phdate">{{ g.date }}</div>
      <div class="photogrid">
        <div v-for="p in g.photos" :key="p.path" class="ph" role="button" tabindex="0"
             @click="viewerIndex = navItems.findIndex(x => x.path === p.path)"
             @keydown.enter="viewerIndex = navItems.findIndex(x => x.path === p.path)">
          <img :src="p.thumb" loading="lazy" :alt="p.name" />
        </div>
      </div>
    </div>
    <div class="empty" v-if="loading">加载中…</div>

    <!-- 统一预览器 -->
    <FileViewer v-if="viewerIndex >= 0 && navItems.length" :items="navItems"
                v-model="viewerIndex" :me="me" @close="viewerIndex = -1"
                @share="onViewerShare" @refresh="applyFilter()" />
    <Modal v-if="sharePaths" title="创建分享" @close="sharePaths = null">
      <ShareModal :paths="sharePaths" @close="sharePaths = null" />
    </Modal>
  </main>
</template>

<style>
.album-row { display: flex; gap: 10px; overflow-x: auto; padding: 2px 2px 8px; }
.album-card {
  position: relative; flex: none; display: grid; gap: 2px; padding: 10px 14px; cursor: pointer;
  border-radius: 12px; border: 1px solid var(--line); background: var(--glass);
  transition: border-color .12s, background .12s;
}
.album-card:hover { border-color: var(--accent); }
.album-card.on { border-color: var(--accent); background: var(--hover); }
.album-title { font-size: 13.5px; font-weight: 600; }
.album-sub { font-size: 11.5px; color: var(--text-3); }
.album-del {
  position: absolute; top: 4px; right: 4px; border: 0; background: transparent;
  color: var(--text-3); font-size: 10px; cursor: pointer; border-radius: 999px;
  width: 18px; height: 18px; line-height: 18px; padding: 0;
}
.album-del:hover { color: var(--danger); }
.facet-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
.facet-sep { width: 1px; height: 18px; background: var(--line); margin: 0 4px; }
.chip {
  display: inline-block; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  border: 1px solid var(--line); background: var(--glass); border-radius: 999px;
  padding: 4px 12px; font-size: 12.5px; cursor: pointer; color: var(--text-2);
  transition: border-color .12s, background .12s;
}
.chip small { color: var(--text-3); margin-left: 2px; }
.chip:hover { border-color: var(--accent); }
.chip.on { border-color: var(--accent); background: var(--hover); color: var(--accent); font-weight: 600; }
.chip.clear { border-style: dashed; }
</style>
