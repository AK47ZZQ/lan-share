<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login as authLogin, refreshAuth } from '../auth'
import { api } from '../api'
import { toast } from '../ui'
import CodeField from '../components/CodeField.vue'

const username = ref('')
const password = ref('')
const totpCode = ref('')
const totpStep = ref(false)
const busy = ref(false)
const err = ref('')
const codeRef = ref<InstanceType<typeof CodeField> | null>(null)
// 认证器不可用时切恢复码（8 位 hex，文本框输入）
const rcMode = ref(false)
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
    const r = await authLogin(username.value, password.value)
    if (r && 'totp_required' in r && r.totp_required) { totpStep.value = true; err.value = ''; return }
    toast('欢迎回来', 'ok')
    const next = String(route.query.next || '/')
    router.replace(next.startsWith('/') ? next : '/')
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitTotp() {
  if (busy.value) return
  const code = totpCode.value.trim()
  if (!code) return
  err.value = ''
  busy.value = true
  try {
    await api.loginTotp(username.value, password.value, code)
    await refreshAuth()
    toast('欢迎回来', 'ok')
    const next = String(route.query.next || '/')
    router.replace(next.startsWith('/') ? next : '/')
  } catch (e) {
    err.value = (e as Error).message
    totpCode.value = ''
    codeRef.value?.reset()
  } finally { busy.value = false }
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

      <form v-if="totpStep" @submit.prevent="submitTotp">
        <p class="sub" style="margin:0 0 10px">{{ rcMode
          ? '输入开通两步验证时保存的一枚恢复码（用一枚少一枚）'
          : '两步验证已启用，请输入认证器中的 6 位动态码' }}</p>
        <CodeField v-if="!rcMode" ref="codeRef" :disabled="busy" :autofocus="true"
                   @update:code="totpCode = $event" @complete="submitTotp" />
        <input v-else v-model="totpCode" placeholder="恢复码（如 a1b2c3d4 或 a1b2-c3d4）"
               autocomplete="off" autofocus style="text-transform:uppercase" />
        <button class="btn" type="submit"
                :disabled="busy || totpCode.length < (rcMode ? 8 : 6)">
          {{ busy ? '验证中…' : '验证并登录' }}
        </button>
        <p class="sub" style="text-align:center;margin:0">
          <a v-if="!rcMode" href="#" @click.prevent="rcMode = true; totpCode = ''; err = ''">认证器不可用？使用恢复码登录</a>
          <a v-else href="#" @click.prevent="rcMode = false; totpCode = ''; err = ''">返回输入动态码</a>
          <span style="opacity:.4">·</span>
          <a href="#" @click.prevent="totpStep = false; rcMode = false; err = ''">返回重新登录</a>
        </p>
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
