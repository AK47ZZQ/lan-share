<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type Me } from './api'
import { toasts } from './ui'

const me = ref<Me | null>(null)
const ready = ref(false)
const disk = ref<{ free: string; warning: boolean } | null>(null)
const route = useRoute()
const router = useRouter()

// 无需登录的公开页
const publicRoute = computed(() => ['share', 'login'].includes(String(route.name)))

async function boot() {
  try {
    me.value = await api.me()
  } catch { /* 未登录 */ }
  ready.value = true
  if (me.value) {
    api.disk().then(d => { disk.value = d }).catch(() => {})
  }
}

watchEffect(() => {
  if (!ready.value) return
  if (!me.value && !publicRoute.value) void router.replace('/login')
  if (me.value && route.name === 'login') void router.replace('/')
})

function logout() {
  api.logout().then(() => { me.value = null; router.push('/login') })
}

function initial(name: string) { return (name || '?').slice(0, 1).toUpperCase() }

onMounted(boot)
</script>

<template>
  <div class="toasts">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{ t.text }}</div>
  </div>

  <div v-if="!ready" class="page"><div class="card empty">加载中…</div></div>

  <template v-else>
    <!-- 公开页（分享/登录）不显示顶栏 -->
    <router-view v-if="publicRoute" />

    <template v-else-if="me">
      <header class="topbar">
        <div class="brand"><span class="logo">◈</span> LAN Share</div>
        <nav class="tabs">
          <router-link to="/">文件</router-link>
          <router-link to="/photos">照片</router-link>
          <router-link to="/trash">回收站</router-link>
          <router-link v-if="me.role === 'admin'" to="/admin">管理</router-link>
        </nav>
        <span class="disk-badge" :class="{ low: disk?.warning }" v-if="disk">
          {{ disk.free }} 可用
        </span>
        <div class="userchip">
          <span class="avatar">{{ initial(me.username) }}</span>
          <span class="uname">{{ me.username }}</span>
          <button class="iconbtn" title="退出" @click="logout">⏻</button>
        </div>
      </header>
      <router-view :me="me" @refresh-me="boot" :key="route.fullPath" />
    </template>

    <div v-else class="page"><div class="card empty">跳转登录…</div></div>
  </template>
</template>

<style>
.uname { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 640px) {
  .disk-badge, .uname { display: none; }
  nav.tabs a { padding: 7px 10px; font-size: 13px; }
}
</style>
