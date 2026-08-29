<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { toast } from '../ui'

const username = ref('')
const password = ref('')
const busy = ref(false)
const err = ref('')
const route = useRoute()
const router = useRouter()

// 邀请码建号（管理面板生成 /login?invite=code）
const inviteCode = ref(String(route.query.invite || ''))
const redeeming = ref(!!inviteCode.value)
const rUsername = ref('')
const rPassword = ref('')

async function submit() {
  err.value = ''
  busy.value = true
  try {
    await api.login(username.value, password.value)
    toast('欢迎回来', 'ok')
    const next = String(route.query.next || '/')
    router.replace(next.startsWith('/') ? next : '/')
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function redeem() {
  err.value = ''
  busy.value = true
  try {
    const r = await fetch('/api/invites/redeem', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: inviteCode.value, username: rUsername.value, password: rPassword.value }),
    }).then(r => r.json())
    if (r.ok) {
      toast(`账号「${r.username}」创建成功，请登录`, 'ok')
      redeeming.value = false
      username.value = r.username
    } else err.value = typeof r.detail === 'string' ? r.detail : '邀请码无效'
  } catch (e) {
    err.value = (e as Error).message
  } finally { busy.value = false }
}
</script>

<template>
  <main class="login-wrap">
    <div class="card login-card">
      <div class="login-logo"><span class="logo big">◈</span></div>
      <h1>{{ redeeming ? '加入家庭空间' : 'LAN Share' }}</h1>
      <p class="sub">{{ redeeming ? '用邀请码创建你的账号' : '家庭团队网盘' }}</p>

      <form v-if="redeeming" @submit.prevent="redeem">
        <input v-model="inviteCode" placeholder="邀请码" />
        <input v-model="rUsername" placeholder="设置用户名" autocomplete="username" />
        <input v-model="rPassword" type="password" placeholder="设置密码（至少 8 位）" autocomplete="new-password" />
        <button class="btn" type="submit" :disabled="busy || !inviteCode || !rUsername || !rPassword">
          {{ busy ? '创建中…' : '创建账号' }}
        </button>
        <p class="sub" style="text-align:center;margin:0"><a href="#" @click.prevent="redeeming = false">已有账号？去登录</a></p>
      </form>

      <form v-else @submit.prevent="submit">
        <input v-model="username" placeholder="用户名" autocomplete="username" autofocus />
        <input v-model="password" type="password" placeholder="密码" autocomplete="current-password" />
        <button class="btn" type="submit" :disabled="busy || !username || !password">
          {{ busy ? '登录中…' : '登录' }}
        </button>
        <p class="sub" style="text-align:center;margin:0">
          <a href="#" @click.prevent="redeeming = true">使用邀请码加入</a>
        </p>
      </form>
      <p v-if="err" class="err" style="text-align:center">{{ err }}</p>
    </div>
  </main>
</template>

<style>
.login-wrap { min-height: 100vh; display: grid; place-items: center; padding: 20px; }
.login-card { width: min(380px, 92vw); padding: 38px 34px 30px; text-align: center; }
.login-logo { display: flex; justify-content: center; margin-bottom: 10px; }
.logo.big { width: 54px; height: 54px; font-size: 26px; border-radius: 16px; }
.login-card h1 { margin: 0; font-size: 24px; letter-spacing: .3px; }
.login-card .sub { margin: 4px 0 22px; color: var(--text-3); font-size: 13.5px; }
.login-card form { display: grid; gap: 11px; text-align: left; }
.login-card .btn { justify-content: center; margin-top: 4px; }
</style>
