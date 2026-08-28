<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Entry, type Me } from './api'

const me = ref<Me | null>(null)
const authState = ref<'loading' | 'login' | 'ready'>('loading')
const username = ref('')
const password = ref('')
const loginError = ref('')

const path = ref('')
const items = ref<Entry[]>([])
const error = ref('')
const uploading = ref(false)

const crumbs = computed(() => {
  const parts = path.value.split('/').filter(Boolean)
  const out = [{ name: '根目录', path: '' }]
  parts.forEach((p, i) => out.push({ name: p, path: parts.slice(0, i + 1).join('/') }))
  return out
})

async function load(p: string) {
  error.value = ''
  try {
    const r = await api.list(p)
    path.value = r.path
    items.value = r.items
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function doLogin() {
  loginError.value = ''
  try {
    await api.login(username.value, password.value)
    await boot()
  } catch (e) {
    loginError.value = (e as Error).message
  }
}

async function doLogout() {
  await api.logout()
  me.value = null
  authState.value = 'login'
}

async function onPickFiles(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (!input.files?.length) return
  uploading.value = true
  error.value = ''
  try {
    for (const f of Array.from(input.files)) {
      await api.upload(path.value, f)
    }
    await load(path.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function newFolder() {
  const name = prompt('文件夹名称')
  if (!name) return
  try {
    await api.mkdir([path.value, name].filter(Boolean).join('/'))
    await load(path.value)
  } catch (e) {
    error.value = (e as Error).message
  }
}

function open(e: Entry) {
  if (e.is_dir) void load(e.path)
  else window.open(`/preview/${encodeURI(e.path)}`, '_blank')
}

async function boot() {
  try {
    me.value = await api.me()
    authState.value = 'ready'
    await load('')
  } catch {
    authState.value = 'login'
  }
}

onMounted(boot)
</script>

<template>
  <main class="shell">
    <div v-if="authState === 'loading'" class="card">加载中…</div>

    <div v-else-if="authState === 'login'" class="card login">
      <h1>LAN Share</h1>
      <form @submit.prevent="doLogin">
        <input v-model="username" placeholder="用户名" autocomplete="username" />
        <input v-model="password" type="password" placeholder="密码" autocomplete="current-password" />
        <button type="submit">登录</button>
        <p v-if="loginError" class="err">{{ loginError }}</p>
      </form>
    </div>

    <div v-else class="card files">
      <header>
        <span class="brand">LAN Share</span>
        <span class="who">{{ me?.username }}（{{ me?.role }}）</span>
        <button @click="doLogout">退出</button>
      </header>

      <nav class="crumbs">
        <template v-for="(c, i) in crumbs" :key="c.path">
          <span v-if="i" class="sep">/</span>
          <a href="#" @click.prevent="load(c.path)">{{ c.name }}</a>
        </template>
      </nav>

      <div class="toolbar">
        <label class="btn">
          <input type="file" multiple hidden @change="onPickFiles" />
          {{ uploading ? '上传中…' : '上传文件' }}
        </label>
        <button class="btn" @click="newFolder">新建文件夹</button>
      </div>

      <p v-if="error" class="err">{{ error }}</p>

      <ul class="listing">
        <li v-if="path" class="row dir">
          <a href="#" @click.prevent="load(crumbs[crumbs.length - 2].path)">↰ 上一级</a>
        </li>
        <li v-for="e in items" :key="e.path" class="row" :class="{ dir: e.is_dir }">
          <a href="#" @click.prevent="open(e)">
            {{ e.is_dir ? '📁' : '📄' }} {{ e.name }}
          </a>
          <span class="meta">{{ e.is_dir ? '' : e.size_human }}</span>
        </li>
        <li v-if="!items.length" class="row empty">（空目录）</li>
      </ul>
    </div>
  </main>
</template>

<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center;
  font-family: system-ui, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: linear-gradient(135deg, #e8ecff, #f6f2ff);
}
.card {
  width: min(720px, 94vw); margin: 24px 0; padding: 24px;
  border-radius: 16px; background: rgba(255, 255, 255, 0.75);
  box-shadow: 0 8px 32px rgba(60, 60, 120, 0.15);
  backdrop-filter: blur(12px);
}
.login h1 { margin: 0 0 16px; }
.login form { display: grid; gap: 10px; }
input {
  padding: 10px 12px; font-size: 16px; border: 1px solid #ccd;
  border-radius: 8px; background: #fff;
}
.btn, form button {
  padding: 9px 14px; border: 0; border-radius: 8px; cursor: pointer;
  background: linear-gradient(135deg, #5b6cff, #8a5bff); color: #fff; font-size: 15px;
}
header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.brand { font-weight: 700; }
.who { color: #667; flex: 1; }
.crumbs { margin-bottom: 10px; font-size: 14px; }
.crumbs a { color: #4a5df0; text-decoration: none; }
.sep { margin: 0 6px; color: #99a; }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.listing { list-style: none; margin: 0; padding: 0; }
.row {
  display: flex; justify-content: space-between; padding: 9px 10px;
  border-radius: 8px;
}
.row:hover { background: rgba(90, 100, 255, 0.08); }
.row a { color: #223; text-decoration: none; word-break: break-all; }
.meta { color: #889; font-size: 13px; white-space: nowrap; margin-left: 12px; }
.err { color: #c0392b; }
.empty { color: #99a; justify-content: center; }
</style>
