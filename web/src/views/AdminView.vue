<script setup lang="ts">
// 成员与权限（v3.49 按 tab 拆分：成员/邀请/授权三页签移居 components/admin/）。
// 父组件只保留数据加载与授权检查；各页签的视图与操作在各自 Pane 内。
import { onMounted, ref } from 'vue'
import { api, type AdminUser, type Grant } from '../api'
import { me } from '../auth'
import UsersPane from '../components/admin/UsersPane.vue'
import InvitesPane from '../components/admin/InvitesPane.vue'
import GrantsPane from '../components/admin/GrantsPane.vue'

const tab = ref<'users' | 'invites' | 'grants'>('users')
const users = ref<AdminUser[]>([])
const invites = ref<{ code: string; role: string; expired: boolean; used: boolean }[]>([])
const grants = ref<Grant[]>([])
const err = ref('')
const dirOptions = ref<{ path: string; label: string }[]>([])

async function loadDirs() {
  // v0.20 授权重构：只取共享空间一级目录（子目录随父级授权自动继承，
  // 服务端 admin_create_grant 也已只收一级路径）
  const out: { path: string; label: string }[] = []
  try {
    const r = await api.list('')
    for (const e of r.items.filter(x => x.is_dir && !x.is_private_root && x.path !== 'private' && x.path !== 'incoming')) {
      if (e.name === 'private' || e.name === 'incoming') continue
      out.push({ path: e.path, label: e.path })
    }
  } catch { /* 无权目录跳过 */ }
  dirOptions.value = out
}

async function loadAll() {
  try {
    ;[users.value, invites.value] = await Promise.all([
      api.adminUsers(), api.adminInvites(),
    ])
    grants.value = await api.adminGrants()
    void loadDirs()
  } catch (e) { err.value = (e as Error).message }
}
onMounted(async () => {
  if (me.value && me.value.role !== 'admin') {
    err.value = '需要管理员权限'
    return
  }
  void loadAll()
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>成员与权限</h1>
      <nav class="tabs" style="flex:1">
        <a href="#" :class="{ 'router-link-active': tab === 'users' }" @click.prevent="tab = 'users'">成员</a>
        <a href="#" :class="{ 'router-link-active': tab === 'invites' }" @click.prevent="tab = 'invites'">邀请</a>
        <a href="#" :class="{ 'router-link-active': tab === 'grants' }" @click.prevent="tab = 'grants'">授权</a>
      </nav>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 成员 -->
    <UsersPane v-if="tab === 'users'" :users="users" @refresh="loadAll" />

    <!-- 邀请 -->
    <InvitesPane v-if="tab === 'invites'" :invites="invites" @refresh="loadAll" />

    <!-- 授权 -->
    <GrantsPane v-if="tab === 'grants'" :users="users" :grants="grants" :dir-options="dirOptions" @refresh="loadAll" />
  </main>
</template>
