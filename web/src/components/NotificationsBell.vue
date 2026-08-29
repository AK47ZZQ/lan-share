<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type NotificationItem } from '../api'
import { fmtTs } from '../ui'

const open = ref(false)
const unread = ref(0)
const items = ref<NotificationItem[]>([])
const loading = ref(false)
let timer: number | undefined
const router = useRouter()

async function load() {
  try {
    const r = await api.notifications()
    unread.value = r.unread
    items.value = r.items
  } catch { /* 会话过期等由全局处理 */ }
}

async function toggle() {
  open.value = !open.value
  if (open.value) {
    loading.value = true
    await load()
    loading.value = false
    if (unread.value) {
      api.notificationsRead().then(() => { unread.value = 0 }).catch(() => {})
    }
  }
}

function go(n: NotificationItem) {
  open.value = false
  if (n.link) router.push(n.link)
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
  <button class="iconbtn bell" :class="{ active: unread }" title="通知"
          @click.stop="toggle">
    🔔
    <span v-if="unread" class="bell-badge">{{ unread > 99 ? '99+' : unread }}</span>
  </button>
  <teleport to="body">
    <div v-if="open" class="notif-panel" @click.stop>
      <div class="notif-head">通知</div>
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
  position: fixed; top: 56px; right: 70px; z-index: 160; width: min(340px, 92vw);
  max-height: 65vh; overflow: auto; border-radius: 14px; padding: 8px;
  background: var(--glass-strong); border: 1px solid var(--glass-border);
  box-shadow: var(--shadow); backdrop-filter: blur(20px); animation: pop .16s ease;
}
.notif-head { font-weight: 600; font-size: 13.5px; padding: 6px 10px 8px; border-bottom: 1px solid var(--line); margin-bottom: 4px; }
.notif-empty { padding: 26px 10px; text-align: center; color: var(--text-3); font-size: 13px; }
.notif-item {
  display: flex; gap: 10px; width: 100%; text-align: left; padding: 9px 10px;
  border: 0; border-radius: 9px; background: transparent; cursor: pointer; color: var(--text);
}
.notif-item:hover { background: var(--hover); }
.notif-item.unread .notif-title { font-weight: 600; }
.notif-icon { font-size: 16px; flex: none; }
.notif-main { display: grid; gap: 2px; min-width: 0; }
.notif-title { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notif-body { font-size: 12.5px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notif-time { font-size: 11.5px; color: var(--text-3); }
@media (max-width: 640px) { .notif-panel { right: 10px; left: 10px; width: auto; } }
</style>
