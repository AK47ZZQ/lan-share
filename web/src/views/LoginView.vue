<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login as authLogin, refreshAuth } from '../auth'
import { api, authApi } from '../api'
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

// ── 首次使用：库里一个用户都没有时，把登录框换成「创建管理员」 ──
//
// 全新安装打开就是登录页，新用户根本不知道该输什么账号密码 —— 服务端
// 有 /api/setup 可以创建首个管理员，但前端从没用过 needs_setup。
// 2026-11-01 用户实测反馈「新用户压根不知道如何登录，没有初始账号和密码」。
const needsSetup = ref(false)
const checkingSetup = ref(true)
const setupName = ref('admin')
const setupPwd = ref('')
const setupPwd2 = ref('')

onMounted(async () => {
  try {
    const st = await authApi.state()
    needsSetup.value = !!st.needs_setup
  } catch {
    // 查不到就按正常登录走 —— 总比卡在空白页强
    needsSetup.value = false
  } finally {
    checkingSetup.value = false
  }
})

async function submitSetup() {
  err.value = ''
  if (setupPwd.value.length < 8) { err.value = '密码至少 8 位'; return }
  if (setupPwd.value !== setupPwd2.value) { err.value = '两次输入的密码不一致'; return }
  busy.value = true
  try {
    await authApi.setup(setupName.value.trim() || 'admin', setupPwd.value)
    // 建完直接登录，省得用户再输一遍
    await authLogin(setupName.value.trim() || 'admin', setupPwd.value)
    await refreshAuth()
    toast('管理员账号已创建', 'ok')
    needsSetup.value = false
    router.replace('/')
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

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
      // 非 JSON 响应（5xx HTML 等）不再抛英文 "Unexpected token"（R1-P3-14）
    }).then(r => r.ok ? r.json() : Promise.reject(new Error(`创建失败（HTTP ${r.status}）`)))
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
      <div class="login-logo"><img class="logo big" src="/static/favicon.svg" alt="LAN Share" /></div>
      <h1>{{ checkingSetup ? 'LAN Share' : needsSetup ? '创建工作账号' : (redeeming ? '加入家庭空间' : 'LAN Share') }}</h1>
      <p class="sub">{{ checkingSetup ? '正在检查…' : needsSetup
        ? '这是第一次使用，先创建一个管理员账号'
        : (redeeming ? '用邀请码创建你的账号' : '家庭团队网盘') }}</p>

      <!-- 首次使用：服务端一个用户都没有，直接引导建管理员 -->
      <form v-if="!checkingSetup && needsSetup" @submit.prevent="submitSetup">
        <input v-model="setupName" placeholder="用户名（默认 admin）" autocomplete="username" />
        <input v-model="setupPwd" type="password" placeholder="密码（至少 8 位）" autocomplete="new-password" />
        <input v-model="setupPwd2" type="password" placeholder="再输一次密码" autocomplete="new-password" />
        <button class="btn" type="submit" :disabled="busy || setupPwd.length < 8 || !setupPwd2">
          {{ busy ? '创建中…' : '创建并登录' }}
        </button>
        <p class="sub" style="text-align:center;margin:0;line-height:1.6">
          这是这台服务器的第一个账号，拥有全部管理权限。<br />
          建好之后，家人可以用你生成的邀请码加入。
        </p>
      </form>

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

      <!-- v-else-if（R1-P1-1）：邀请码建号页此前会同时渲染下方的登录表单 -->
      <!-- 再加 !needsSetup：首次设置时也不能冒出登录框，否则用户不知道该填哪个 -->
      <form v-else-if="!redeeming && !needsSetup" @submit.prevent="submit">
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
.logo.big { width: 54px; height: 54px; border-radius: 16px; display: block; box-shadow: 0 6px 18px rgba(91, 108, 255, .4); }
.login-card h1 { margin: 0; font-size: 24px; letter-spacing: .3px; }
.login-card .sub { margin: 4px 0 22px; color: var(--text-3); font-size: 13.5px; }
.login-card form { display: grid; gap: 11px; text-align: left; }
.login-card .btn { justify-content: center; margin-top: 4px; }
</style>
