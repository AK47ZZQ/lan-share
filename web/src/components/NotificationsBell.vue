<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type NotificationItem, type ActivityItem, type Me } from '../api'
import { fmtTs } from '../ui'
import { ACTION_ICON, activityTitle, relTime } from '../activity'

const props = defineProps<{ me: Me }>()

const open = ref(false)
const tab = ref<'notify' | 'activity'>('notify')
const unread = ref(0)
const items = ref<NotificationItem[]>([])
const activity = ref<ActivityItem[]>([])
const activityLoading = ref(false)
const loading = ref(false)
let timer: number | undefined
const router = useRouter()

// 新动态高亮水位：打开动态 Tab 时刷新
const seenAt = ref(Number(localStorage.getItem('lsActivitySeen') || 0))

async function load() {
  try {
    const r = await api.notifications()
    unread.value = r.unread
    items.value = r.items
  } catch { /* 会话过期等由全局处理 */ }
}

async function loadActivity() {
  activityLoading.value = true
  try { activity.value = await api.activity(40) }
  catch { /* 静默 */ }
  finally { activityLoading.value = false }
}

async function toggle() {
  open.value = !open.value
  if (open.value) {
    if (tab.value === 'notify') {
      loading.value = true
      await load()
      loading.value = false
      if (unread.value) {
        api.notificationsRead().then(() => { unread.value = 0 }).catch(() => {})
      }
    } else {
      await openActivity()
    }
  }
}

async function switchTab(t: 'notify' | 'activity') {
  tab.value = t
  if (t === 'notify') {
    loading.value = true
    await load()
    loading.value = false
    // 重开通知 Tab 同样清除未读（与首次打开一致）
    if (unread.value) {
      api.notificationsRead().then(() => { unread.value = 0 }).catch(() => {})
    }
  } else {
    await openActivity()
  }
}

async function openActivity() {
  await loadActivity()
  seenAt.value = Math.floor(Date.now() / 1000)
  localStorage.setItem('lsActivitySeen', String(seenAt.value))
}

function go(n: NotificationItem) {
  open.value = false
  if (n.link) router.push(n.link)
}

// 动态条目点击 → 独立动态页定位高亮（直达跳转在 /activity 页的"去查看"）
function goActivityItem(n: ActivityItem) {
  open.value = false
  router.push({ path: '/activity', query: { ts: String(n.ts) } })
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 60_000)
  document.addEventListener('click', close)
})
onUnmounted(() => { if (timer) clearInterval(timer); document.removeEventListener('click', close) })
function close() { open.value = false }
</script>

<template>
  <button class="iconbtn bell" :class="{ active: unread }" title="通知与动态"
          @click.stop="toggle">
    🔔
    <span v-if="unread" class="bell-badge">{{ unread > 99 ? '99+' : unread }}</span>
  </button>
  <teleport to="body">
    <div v-if="open" class="notif-panel" @click.stop>
      <div class="notif-tabs">
        <button :class="{ on: tab === 'notify' }" @click="switchTab('notify')">
          通知<span v-if="unread" class="tab-badge">{{ unread }}</span>
        </button>
        <button :class="{ on: tab === 'activity' }" @click="switchTab('activity')">动态</button>
      </div>

      <!-- 通知 Tab -->
      <template v-if="tab === 'notify'">
        <div v-if="loading" class="notif-empty">加载中…</div>
        <div v-else-if="!items.length" class="notif-empty">暂无通知<br /><small>成员分享给你的文件会出现在这里</small></div>
        <div v-else class="notif-list">
          <button v-for="n in items" :key="n.id" class="notif-item" :class="{ unread: !n.read }" @click="go(n)">
            <span class="notif-icon">{{ n.type === 'share' ? '🔗' : '🔔' }}</span>
            <span class="notif-main">
              <span class="notif-title">{{ n.title }}</span>
              <span v-if="n.body" class="notif-body">{{ n.body }}</span>
              <span class="notif-time">{{ fmtTs(n.created_at) }}</span>
            </span>
          </button>
        </div>
      </template>

      <!-- 动态 Tab -->
      <template v-else>
        <div v-if="activityLoading" class="notif-empty">加载中…</div>
        <div v-else-if="!activity.length" class="notif-empty">还没有动态<br /><small>家人的上传、分享都会出现在这里</small></div>
        <template v-else>
          <div class="notif-list">
            <button v-for="(n, i) in activity" :key="i" class="notif-item act clickable"
                    :title="'在动态页中查看'" @click="goActivityItem(n)">
              <span class="notif-icon">{{ ACTION_ICON[n.action] || '🔔' }}</span>
              <span class="notif-main">
                <span class="notif-title">
                  {{ activityTitle(n) }}
                  <span v-if="n.ts > seenAt" class="new-dot" title="新动态" />
                </span>
                <span class="notif-body">{{ n.target }}</span>
                <span class="notif-time">{{ relTime(n.ts) }}</span>
              </span>
            </button>
          </div>
          <div style="text-align:center;padding:6px 0 2px">
            <router-link class="notif-more" to="/activity" @click="open = false">查看全部动态 →</router-link>
          </div>
        </template>
      </template>
    </div>
  </teleport>
</template>

<style>
.bell { position: relative; }
.bell-badge {
  position: absolute; top: -4px; right: -6px; min-width: 16px; height: 16px;
  border-radius: 999px; background: var(--danger, #e5484d); color: #fff;
  font-size: 10px; line-height: 16px; padding: 0 4px; font-weight: 700;
}
.notif-panel {
  position: fixed; top: 56px; right: 70px; z-index: 160; width: min(360px, 92vw);
  max-height: 68vh; overflow: auto; border-radius: 14px; padding: 8px;
  background: var(--glass-strong); border: 1px solid var(--glass-border);
  box-shadow: var(--shadow); backdrop-filter: blur(20px); animation: pop .16s ease;
}
.notif-tabs {
  display: flex; gap: 4px; padding: 4px; margin-bottom: 4px;
  border-bottom: 1px solid var(--line);
}
.notif-tabs button {
  flex: 1; border: 0; background: transparent; color: var(--text-3);
  padding: 7px 4px; font-size: 13px; cursor: pointer; border-radius: 8px;
  display: flex; align-items: center; justify-content: center; gap: 6px;
}
.notif-tabs button.on { background: var(--hover); color: var(--accent); font-weight: 600; }
.tab-badge {
  min-width: 16px; height: 16px; border-radius: 999px; background: var(--danger, #e5484d);
  color: #fff; font-size: 10px; line-height: 16px; padding: 0 4px; font-weight: 700;
}
.notif-empty { padding: 26px 10px; text-align: center; color: var(--text-3); font-size: 13px; }
.notif-item {
  display: flex; gap: 10px; width: 100%; text-align: left; padding: 9px 10px;
  border: 0; border-radius: 9px; background: transparent; cursor: pointer; color: var(--text);
}
.notif-item:hover { background: var(--hover); }
.notif-item.unread .notif-title { font-weight: 600; }
.notif-item.act.clickable .notif-title { color: var(--text); }
.notif-item.act { cursor: default; }
.notif-item.act.clickable { cursor: pointer; }
.notif-more { font-size: 12.5px; color: var(--accent); text-decoration: none; }
.notif-more:hover { text-decoration: underline; }
.notif-icon { font-size: 16px; flex: none; }
.notif-main { display: grid; gap: 2px; min-width: 0; }
.notif-title {
  font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  display: flex; align-items: center; gap: 6px;
}
.notif-body { font-size: 12.5px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notif-time { font-size: 11.5px; color: var(--text-3); }
.new-dot {
  flex: none; width: 7px; height: 7px; border-radius: 999px; background: var(--accent);
}
@media (max-width: 640px) { .notif-panel { right: 10px; left: 10px; width: auto; } }
</style>
