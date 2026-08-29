<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { authReady, logout as authLogout, me, refreshAuth } from './auth'
import { toasts } from './ui'

const disk = ref<{ free: string; warning: boolean; disk_used_pct?: number } | null>(null)
const route = useRoute()
const router = useRouter()

// 无需登录的公开页
const publicRoute = computed(() => ['share', 'login'].includes(String(route.name)))

async function boot() {
  await refreshAuth()
  if (me.value) api.disk().then(d => { disk.value = d }).catch(() => {})
}

const menuOpen = ref(false)
function closeMenu() { menuOpen.value = false }
function doLogout() { closeMenu(); authLogout().then(() => router.replace('/login')) }

function initial(name: string) { return (name || '?').slice(0, 1).toUpperCase() }

onMounted(() => { boot(); document.addEventListener('click', closeMenu) })

watchEffect(() => {
  if (!authReady.value) return
  if (!me.value && !publicRoute.value) void router.replace('/login')
  if (me.value && route.name === 'login') void router.replace('/')
})
</script>

<template>
  <div class="toasts">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{ t.text }}</div>
  </div>

  <div v-if="!authReady" class="page"><div class="card empty">加载中…</div></div>

  <template v-else>
    <!-- 公开页（分享/登录）不显示顶栏 -->
    <router-view v-if="publicRoute" />

    <template v-else-if="me">
      <header class="topbar">
        <div class="brand"><span class="logo">◈</span><span class="brand-name">LAN Share</span></div>
        <span class="disk-badge" :class="{ low: disk?.warning }" v-if="disk" title="磁盘已用比例">
          <span class="bar"><i :style="{ width: Math.round(disk.disk_used_pct ?? 0) + '%' }" /></span>
          {{ disk.free }} 可用
        </span>
        <button class="userchip avatar-btn" @click.stop="menuOpen = !menuOpen">
          <span class="uname">{{ me.username }}</span>
          <span class="avatar">{{ initial(me.username) }}</span>
          <span class="caret">▾</span>
        </button>
        <teleport to="body">
          <div v-if="menuOpen" class="usermenu" @click.stop>
            <div class="um-head">
              <span class="avatar">{{ initial(me.username) }}</span>
              <div><div class="um-name">{{ me.username }}</div>
                <div class="um-role">{{ me.role === 'admin' ? '管理员' : me.role === 'guest' ? '访客' : '成员' }}</div></div>
            </div>
            <router-link class="um-item" to="/" @click="menuOpen = false"><span>🗂️</span> 文件</router-link>
            <router-link class="um-item" to="/photos" @click="menuOpen = false"><span>🖼️</span> 照片</router-link>
            <router-link class="um-item" to="/trash" @click="menuOpen = false"><span>🗑️</span> 回收站</router-link>
            <router-link v-if="me.role === 'admin'" class="um-item" to="/admin" @click="menuOpen = false"><span>⚙️</span> 管理</router-link>
            <div class="um-divider" />
            <button class="um-item danger" @click="doLogout"><span>⏻</span> 退出登录</button>
          </div>
        </teleport>
      </header>
      <router-view :me="me" :key="route.fullPath" />
    </template>

    <div v-else class="page"><div class="card empty">跳转登录…</div></div>
  </template>
</template>

<style>
.uname { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.avatar-btn {
  margin-left: auto; display: flex; align-items: center; gap: 8px;
  background: var(--glass-strong); border: 1px solid var(--line);
  border-radius: 999px; padding: 4px 10px 4px 6px; cursor: pointer; color: var(--text-2);
  transition: background .12s, border-color .12s;
}
.avatar-btn:hover { background: var(--hover); border-color: var(--accent); }
.avatar-btn .caret { font-size: 10px; color: var(--text-3); }
.usermenu {
  position: fixed; top: 56px; right: 14px; z-index: 160; min-width: 200px; padding: 8px;
  border-radius: 14px; background: var(--glass-strong); border: 1px solid var(--glass-border);
  box-shadow: var(--shadow); backdrop-filter: blur(20px); animation: pop .16s ease;
}
.um-head { display: flex; gap: 10px; align-items: center; padding: 8px 10px 12px; border-bottom: 1px solid var(--line); margin-bottom: 6px; }
.um-name { font-weight: 600; font-size: 14px; }
.um-role { font-size: 12px; color: var(--text-3); }
.um-item {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 9px 11px; border: 0; border-radius: 9px; cursor: pointer;
  background: transparent; color: var(--text); font-size: 14px; text-decoration: none;
}
.um-item:hover { background: var(--hover); }
.um-item.danger { color: var(--danger); }
.um-divider { height: 1px; margin: 5px 8px; background: var(--line); }
@media (max-width: 640px) {
  .disk-badge .bar, .uname, .brand-name { display: none; }
  .topbar { gap: 8px; padding: 9px 12px; }
  .disk-badge { margin-left: auto; }
  .usermenu { right: 10px; left: 10px; }
}
</style>
