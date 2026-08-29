<script setup lang="ts">
import { ref } from 'vue'
import { api, type Me } from '../api'
import { refreshAuth } from '../auth'
import { toast } from '../ui'

const props = defineProps<{ me: Me }>()
const emit = defineEmits<{ close: []; updated: [] }>()

// 改用户名
const newName = ref('')
const namePwd = ref('')
const nameBusy = ref(false)
const nameErr = ref('')

// 改密码
const oldPwd = ref('')
const newPwd = ref('')
const pwdBusy = ref(false)
const pwdErr = ref('')

// 两步验证（TOTP）
const totpEnabled = ref(!!props.me.totp_enabled)
const totpSecret = ref('')
const totpUri = ref('')
const totpCode = ref('')
const totpBusy = ref(false)
const totpErr = ref('')
const totpDisablePwd = ref('')

async function totpSetup() {
  totpErr.value = ''
  totpBusy.value = true
  try {
    const r = await api.totpSetup()
    totpSecret.value = r.secret
    totpUri.value = r.otpauth
  } catch (e) { totpErr.value = (e as Error).message } finally { totpBusy.value = false }
}

async function totpEnable() {
  totpErr.value = ''
  totpBusy.value = true
  try {
    await api.totpEnable(totpCode.value)
    totpEnabled.value = true
    totpSecret.value = ''; totpUri.value = ''; totpCode.value = ''
    toast('两步验证已启用，下次登录需输入动态验证码', 'ok')
    await refreshAuth()
    emit('updated')
  } catch (e) { totpErr.value = (e as Error).message } finally { totpBusy.value = false }
}

async function totpDisable() {
  totpErr.value = ''
  totpBusy.value = true
  try {
    await api.totpDisable(totpDisablePwd.value)
    totpEnabled.value = false
    totpDisablePwd.value = ''
    toast('两步验证已关闭', 'ok')
    await refreshAuth()
    emit('updated')
  } catch (e) { totpErr.value = (e as Error).message } finally { totpBusy.value = false }
}

async function copySecret() {
  try { await navigator.clipboard.writeText(totpSecret.value); toast('密钥已复制', 'ok') }
  catch { toast('复制失败，请手动记录密钥', 'err') }
}

async function doRename() {
  nameErr.value = ''
  nameBusy.value = true
  try {
    const r = await api.changeUsername(newName.value.trim(), namePwd.value)
    toast(`用户名已改为「${r.username}」`, 'ok')
    await refreshAuth()
    emit('updated')
    emit('close')
  } catch (e) { nameErr.value = (e as Error).message } finally { nameBusy.value = false }
}

async function doPwd() {
  pwdErr.value = ''
  pwdBusy.value = true
  try {
    await api.changePassword(oldPwd.value, newPwd.value)
    toast('密码已修改（其他设备已全部登出）', 'ok')
    await refreshAuth()
    emit('close')
  } catch (e) { pwdErr.value = (e as Error).message } finally { pwdBusy.value = false }
}
</script>

<template>
  <teleport to="body">
    <div class="overlay" @click.self="emit('close')">
      <div class="modal-card">
        <h3>账号设置</h3>
        <p style="font-size:13px;color:var(--text-3);margin:0 0 14px">
          当前账号：<b>{{ props.me.username }}</b>（{{ props.me.role === 'admin' ? '管理员' : props.me.role === 'guest' ? '访客' : '成员' }}）
        </p>

        <form @submit.prevent="doRename" style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">✏️ 修改用户名</div>
          <input v-model="newName" placeholder="新用户名" autocomplete="username" />
          <input v-model="namePwd" type="password" placeholder="当前密码（确认）" autocomplete="current-password" />
          <button class="btn sm" :disabled="nameBusy || !newName || !namePwd">
            {{ nameBusy ? '提交中…' : '修改用户名' }}
          </button>
          <p v-if="nameErr" class="err" style="margin:0">{{ nameErr }}</p>
        </form>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <form @submit.prevent="doPwd" style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🔑 修改密码</div>
          <input v-model="oldPwd" type="password" placeholder="当前密码" autocomplete="current-password" />
          <input v-model="newPwd" type="password" placeholder="新密码（至少 8 位）" autocomplete="new-password" />
          <button class="btn sm" :disabled="pwdBusy || !oldPwd || !newPwd">
            {{ pwdBusy ? '提交中…' : '修改密码' }}
          </button>
          <p style="font-size:12px;color:var(--text-3);margin:0">修改后其他设备的登录会全部失效，当前设备保持登录。</p>
          <p v-if="pwdErr" class="err" style="margin:0">{{ pwdErr }}</p>
        </form>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <!-- 两步验证 -->
        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🛡️ 两步验证（TOTP）</div>
          <template v-if="totpEnabled">
            <p style="font-size:12.5px;color:var(--text-3);margin:0">已启用。登录时需要输入认证器（如 Google Authenticator、Microsoft Authenticator）的 6 位动态码。</p>
            <input v-model="totpDisablePwd" type="password" placeholder="当前密码（确认关闭）" autocomplete="current-password" />
            <button class="btn danger sm" :disabled="totpBusy || !totpDisablePwd" @click="totpDisable">
              {{ totpBusy ? '提交中…' : '关闭两步验证' }}
            </button>
          </template>
          <template v-else-if="totpUri">
            <p style="font-size:12.5px;color:var(--text-2);margin:0">1️⃣ 用认证器扫描二维码，或手动输入密钥：</p>
            <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
              <img :src="`/api/qr?text=${encodeURIComponent(totpUri)}`"
                   style="width:150px;height:150px;background:#fff;padding:6px;border-radius:10px" alt="二维码" />
              <div style="display:grid;gap:6px;font-size:12.5px">
                <code style="user-select:all;background:var(--glass);padding:6px 8px;border-radius:8px;border:1px solid var(--line)">{{ totpSecret }}</code>
                <button class="btn ghost sm" @click="copySecret">📋 复制密钥</button>
              </div>
            </div>
            <p style="font-size:12.5px;color:var(--text-2);margin:0">2️⃣ 输入认证器显示的 6 位动态码完成开通：</p>
            <input v-model="totpCode" placeholder="6 位动态验证码" inputmode="numeric" maxlength="6" style="width:180px" @keydown.enter="totpEnable" />
            <div style="display:flex;gap:8px">
              <button class="btn sm" :disabled="totpBusy || totpCode.length !== 6" @click="totpEnable">确认开通</button>
              <button class="btn ghost sm" @click="totpUri = ''; totpSecret = ''; totpCode = ''">取消</button>
            </div>
          </template>
          <template v-else>
            <p style="font-size:12.5px;color:var(--text-3);margin:0">为账号增加一层保护：登录除密码外还需手机认证器的动态码。管理员重置密码会同步解除。</p>
            <button class="btn sm" :disabled="totpBusy" @click="totpSetup">启用两步验证</button>
          </template>
          <p v-if="totpErr" class="err" style="margin:0">{{ totpErr }}</p>
        </div>

        <div class="actions"><button class="btn ghost sm" @click="emit('close')">关闭</button></div>
      </div>
    </div>
  </teleport>
</template>
