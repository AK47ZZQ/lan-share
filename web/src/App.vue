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

function logout() {
  authLogout().then(() => router.replace('/login'))
}

function initial(name: string) { return (name || '?').slice(0, 1).toUpperCase() }

onMounted(boot)

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
        <nav class="tabs">
          <router-link to="/">文件</router-link>
          <router-link to="/photos">照片</router-link>
          <router-link to="/trash">回收站</router-link>
          <router-link v-if="me.role === 'admin'" to="/admin">管理</router-link>
        </nav>
        <span class="disk-badge" :class="{ low: disk?.warning }" v-if="disk" title="磁盘已用比例">
          <span class="bar"><i :style="{ width: Math.round(disk.disk_used_pct ?? 0) + '%' }" /></span>
          {{ disk.free }} 可用
        </span>
        <div class="userchip">
          <span class="avatar">{{ initial(me.username) }}</span>
          <span class="uname">{{ me.username }}</span>
          <button class="iconbtn" title="退出" @click="logout">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
              <path d="M12 3v8" /><path d="M6.3 6.5a8 8 0 1 0 11.4 0" />
            </svg>
          </button>
        </div>
      </header>
      <router-view :me="me" :key="route.fullPath" />
    </template>

    <div v-else class="page"><div class="card empty">跳转登录…</div></div>
  </template>
</template>

<style>
.uname { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 640px) {
  .disk-badge, .uname, .brand-name { display: none; }
  .topbar { gap: 8px; padding: 9px 12px; }
  nav.tabs a { padding: 7px 9px; font-size: 13px; }
  .userchip { margin-left: auto; }
}
</style>
