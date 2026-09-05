<script setup lang="ts">
// 家庭动态页（全员）：完整动态流 + 高亮定位 + 每条"去查看"直达
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type ActivityItem, type Me } from '../api'
import { ACTION_ICON, activityTitle, activityRouteFor, relTime } from '../activity'
import { confirmDlg, toast } from '../ui'
const props = defineProps<{ me: Me }>()

const items = ref<ActivityItem[]>([])
const loading = ref(false)
const err = ref('')
const limit = ref(50)
const hasMore = ref(false)
const highlightTs = ref(0)
const route = useRoute()
const router = useRouter()

async function load() {
  loading.value = true
  err.value = ''
  try {
    const r = await api.activity(limit.value)
    items.value = r
    // 服务端硬顶 100 条（admin.py max_items=100）：到顶即藏"加载更多"，
    // 否则按钮永真空转（R1-P2-5）
    hasMore.value = r.length >= Math.min(limit.value, 100) && limit.value < 100
  } catch (e) { err.value = (e as Error).message }
  finally { loading.value = false }
}
function loadMore() {
  limit.value = Math.min(limit.value + 50, 100)
  void load()
}

function go(n: ActivityItem) {
  const r = activityRouteFor(props.me, n)
  // SPA 路由跳转（R1-P3-11）：整页 assign 丢全局状态并白屏重载
  if (r) router.push({ path: r.path, query: r.query })
}
async function clearFeed() {
  if (!(await confirmDlg('清空当前动态？（不影响审计历史记录）', { danger: true, okText: '清空' }))) return
  try {
    const r = await fetch('/api/activity/clear', { method: 'POST' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    await load()
    toast('动态已清空', 'ok')
  } catch (e) { toast((e as Error).message, 'err') }
}

onMounted(async () => {
  highlightTs.value = Number(route.query.ts || 0)
  await load()
  if (highlightTs.value) {
    setTimeout(() => {
      document.querySelector('.act-item.hl')?.scrollIntoView({ block: 'center' })
    }, 100)
  }
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>家庭动态</h1>
      <span class="badge" v-if="items.length">{{ items.length }} 条</span>
      <button v-if="me.role === 'admin' && items.length" class="btn ghost sm" style="margin-left:auto"
              @click="clearFeed">清空动态</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div v-if="!items.length && !loading" class="card empty-state">
      <div class="art">🔔</div>
      <div>还没有动态</div>
      <div class="hint">家人的上传、分享、删除都会出现在这里</div>
    </div>

    <div class="card act-list" style="padding:8px">
      <div v-for="(n, i) in items" :key="i" class="frow act-item" :class="{ hl: n.ts === highlightTs }">
        <span class="icon">{{ ACTION_ICON[n.action] || '🔔' }}</span>
        <span class="name">
          <div>{{ activityTitle(n) }} <span v-if="n.count > 1" class="act-count">×{{ n.count }}</span></div>
          <small>{{ n.target }}</small>
        </span>
        <span class="act-right">
          <span class="act-time">{{ relTime(n.ts) }}</span>
          <button v-if="activityRouteFor(me, n)" class="btn ghost sm" @click="go(n)">去查看 →</button>
        </span>
      </div>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="hasMore" style="text-align:center;padding:10px">
        <button class="btn ghost sm" @click="loadMore">加载更多</button>
      </div>
    </div>
  </main>
</template>

<style>
.act-item.hl { background: var(--hover); box-shadow: inset 3px 0 0 var(--accent); border-radius: 9px; }
.act-count {
  font-size: 11px; color: var(--accent); border: 1px solid var(--accent);
  border-radius: 999px; padding: 0 7px; margin-left: 4px;
}
.act-right { display: grid; gap: 4px; justify-items: end; flex: none; }
.act-time { font-size: 12px; color: var(--text-3); }
</style>
