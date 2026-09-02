<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { authReady, logout as authLogout, me, refreshAuth } from './auth'
import { dlg, dlgCancel, dlgOk, toasts } from './ui'
import { previewAs, setPreviewAs } from './previewAs'
import DiskModal from './components/DiskModal.vue'
import AccountModal from './components/AccountModal.vue'
import NotificationsBell from './components/NotificationsBell.vue'
import { applyTheme, currentTheme, type Theme } from './theme'

const disk = ref<{ free: string; warning: boolean; used_bytes: number; total_bytes: number } | null>(null)
const diskPct = computed(() => disk.value && disk.value.total_bytes ? Math.round(disk.value.used_bytes / disk.value.total_bytes * 100) : 0)
const route = useRoute()
const router = useRouter()

// 公开渲染的页面：分享落地页、登录页、直传页（未登录时页内引导登录）
const publicRoute = computed(() => ['share', 'login', 'up'].includes(String(route.name)))

async function boot() {
  await refreshAuth()
  if (me.value) api.disk().then(d => { disk.value = d }).catch(() => {})
}

const menuOpen = ref(false)
const showDisk = ref(false)
const showAccount = ref(false)
const theme = ref<Theme>(currentTheme())
function setTheme(th: Theme) { theme.value = th; applyTheme(th) }
function closeMenu() { menuOpen.value = false }
function doLogout() { closeMenu(); authLogout().then(() => router.replace('/login')) }

function initial(name: string) { return (name || '?').slice(0, 1).toUpperCase() }

function exitPreview() {
  setPreviewAs('')
  void router.push('/admin')
}

onMounted(() => { boot(); document.addEventListener('click', closeMenu) })

watchEffect(() => {
  // 首帧路由可能尚未解析（route.name === undefined），此时禁止重定向，
  // 否则直接打开 /s/ 分享链接的未登录用户会被误弹到登录页
  if (!authReady.value || route.name === undefined) return
  if (!me.value && !publicRoute.value) void router.replace('/login')
  if (me.value && route.name === 'login') void router.replace('/')
})
</script>

<template>
  <div class="toasts">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{ t.text }}</div>
  </div>

  <!-- 全局确认/输入对话框（Promise 化）；z 高于业务弹窗——动态 teleport 的
       模态会 append 到 body 末尾，同 z-index 时会反过来盖住本对话框 -->
  <teleport to="body">
    <div v-if="dlg.open" class="overlay dlg-overlay" @click.self="dlgCancel">
      <div class="modal-card" style="width:min(400px,92vw)">
        <h3>{{ dlg.title }}</h3>
        <p style="font-size:14px;margin:0 0 14px">{{ dlg.text }}</p>
        <input v-if="dlg.kind === 'prompt'" v-model="dlg.value" :placeholder="dlg.placeholder"
               style="width:100%" @keydown.enter="dlgOk" autofocus />
        <div class="actions">
          <button class="btn ghost sm" @click="dlgCancel">取消</button>
          <button class="btn sm" :class="{ danger: dlg.danger }" @click="dlgOk">
            {{ dlg.okText || (dlg.kind === 'prompt' ? '确定' : dlg.danger ? '确认删除' : '确认') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>

  <div v-if="!authReady" class="page"><div class="card empty">加载中…</div></div>

  <template v-else>
    <!-- 公开页（分享/登录）不显示顶栏 -->
    <router-view v-if="publicRoute" />

    <template v-else-if="me">
      <!-- 成员视角预览横幅（admin 专用） -->
      <div v-if="previewAs" style="position:sticky;top:0;z-index:70;display:flex;gap:10px;align-items:center;justify-content:center;padding:7px 12px;font-size:13px;background:var(--accent,#4f7cff);color:#fff">
        <span>👁 正在以 <b>{{ previewAs }}</b> 的视角浏览（只读预览，操作仍按你的管理员权限执行）</span>
        <button class="btn sm" style="background:#fff;color:#1f2430;border:none;font-weight:600" @click="exitPreview">退出视角</button>
      </div>
      <header class="topbar">
        <router-link to="/" class="brand" title="回到主页">
          <span class="logo">◈</span><span class="brand-name">LAN Share</span>
        </router-link>
        <button class="disk-badge" :class="{ low: disk?.warning }" v-if="disk"
                :title="`磁盘已用 ${diskPct}%，点击查看详情`" @click.stop="showDisk = true">
          <span class="bar"><i :style="{ width: diskPct + '%' }" /></span>
          {{ diskPct }}% · {{ disk.free }} 可用
        </button>
        <NotificationsBell v-if="me" :me="me" />
        <button class="userchip avatar-btn" @click.stop="menuOpen = !menuOpen">
          <span class="uname">{{ me.username }}</span>
          <span class="avatar">{{ initial(me.username) }}</span>
          <span class="caret">▾</span>
        </button>
        <DiskModal v-if="showDisk" :me="me" @close="showDisk = false" />
        <AccountModal v-if="showAccount" :me="me" @close="showAccount = false" @updated="boot" />
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
            <router-link class="um-item" to="/shares" @click="menuOpen = false"><span>🔗</span> 分享管理</router-link>
            <router-link class="um-item" to="/activity" @click="menuOpen = false"><span>🔔</span> 家庭动态</router-link>
            <router-link v-if="me.role === 'admin'" class="um-item" to="/admin" @click="menuOpen = false"><span>⚙️</span> 管理</router-link>
            <button class="um-item" @click="closeMenu(); showDisk = true"><span>💾</span> 存储空间</button>
            <div class="um-divider" />
            <div class="um-theme">
              <span style="font-size:13px;color:var(--text-3)">主题</span>
              <div class="theme-seg">
                <button :class="{ on: theme === 'light' }" @click="setTheme('light')">浅色</button>
                <button :class="{ on: theme === 'dark' }" @click="setTheme('dark')">深色</button>
                <button :class="{ on: theme === 'auto' }" @click="setTheme('auto')">跟随系统</button>
              </div>
            </div>
            <div class="um-divider" />
            <button class="um-item" @click="closeMenu(); showAccount = true"><span>🔑</span> 账号设置</button>
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
/* 全局确认/输入对话框必须压过所有业务弹窗（模态 overlay=400，toast=500） */
.overlay.dlg-overlay { z-index: 450; }
.um-theme { padding: 6px 10px 4px; display: grid; gap: 6px; }
.theme-seg { display: flex; border: 1px solid var(--line); border-radius: 9px; overflow: hidden; }
.theme-seg button {
  flex: 1; border: 0; background: transparent; color: var(--text-3);
  padding: 6px 4px; font-size: 12.5px; cursor: pointer;
}
.theme-seg button.on { background: var(--hover); color: var(--accent); font-weight: 600; }
.uname { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.avatar-btn {
  display: flex; align-items: center; gap: 8px;
  background: var(--glass-strong); border: 1px solid var(--line);
  border-radius: 999px; padding: 4px 10px 4px 6px; cursor: pointer; color: var(--text-2);
  transition: background .12s, border-color .12s;
}
/* 铃铛吃掉剩余空间：通知图标靠右、紧贴头像左侧 */
.topbar .bell { margin-left: auto; }
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
  .usermenu { right: 10px; left: 10px; }
}
</style>
