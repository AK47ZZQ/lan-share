<script setup lang="ts">
import { ref } from 'vue'
import { api, type AdminUser } from '../api'
import { confirmDlg, toast } from '../ui'
import Modal from './Modal.vue'

const props = defineProps<{ user: AdminUser }>()
const emit = defineEmits<{ close: []; changed: [] }>()

// 改用户名
const newName = ref(props.user.username)
const nameBusy = ref(false)
const nameErr = ref('')
async function doRename() {
  nameErr.value = ''
  nameBusy.value = true
  try {
    const r = await api.adminSetUsername(props.user.id, newName.value.trim())
    toast(`用户名已改为「${r.username}」（私人目录已同步改名）`, 'ok')
    emit('changed'); emit('close')
  } catch (e) { nameErr.value = (e as Error).message } finally { nameBusy.value = false }
}

// 重置密码
const newPwd = ref('')
const pwdBusy = ref(false)
const pwdErr = ref('')
async function doPwd() {
  pwdErr.value = ''
  pwdBusy.value = true
  try {
    await api.adminSetPassword(props.user.id, newPwd.value)
    toast(`「${props.user.username}」密码已重置（该成员所有设备已登出）`, 'ok')
    emit('changed'); emit('close')
  } catch (e) { pwdErr.value = (e as Error).message } finally { pwdBusy.value = false }
}

// WebDAV 独立密码
const davPwd = ref('')
const davBusy = ref(false)
function genDavPwd() {
  const chars = 'abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789'
  const arr = crypto.getRandomValues(new Uint8Array(14))
  davPwd.value = Array.from(arr, v => chars[v % chars.length]).join('')
}
async function saveDav() {
  davBusy.value = true
  try {
    await api.adminSetWebdavPwd(props.user.id, davPwd.value)
    await navigator.clipboard?.writeText(davPwd.value).catch(() => {})
    toast(`WebDAV 密码已更新并复制到剪贴板（密码会留在剪贴板历史，请尽快粘贴到安全渠道发送）`, 'ok')
    davPwd.value = ''
    emit('changed')
  } catch (e) { toast((e as Error).message, 'err') } finally { davBusy.value = false }
}
async function clearDav() {
  if (!(await confirmDlg(`清除「${props.user.username}」的 WebDAV 独立密码？之后 WebDAV 回退用登录密码挂载。`, { okText: '清除' }))) return
  davBusy.value = true
  try {
    await api.adminSetWebdavPwd(props.user.id, '')
    toast('已清除', 'ok')
    emit('changed')
  } catch (e) { toast((e as Error).message, 'err') } finally { davBusy.value = false }
}

// 仅解除两步验证（不动密码、不登出）
const totpBusy = ref(false)
async function clearTotp() {
  if (!(await confirmDlg(
    `解除「${props.user.username}」的两步验证？成员下次登录不再需要动态码；密码与已登录设备不受影响。`,
    { okText: '解除', danger: true }))) return
  totpBusy.value = true
  try {
    await api.adminTotpDisable(props.user.id)
    toast('两步验证已解除', 'ok')
    emit('changed'); emit('close')
  } catch (e) { toast((e as Error).message, 'err') } finally { totpBusy.value = false }
}
</script>

<template>
  <teleport to="body">
    <div class="overlay" @click.self="emit('close')">
      <div class="modal-card">
        <h3>成员账号 — {{ props.user.username }}</h3>

        <form @submit.prevent="doRename" style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">✏️ 修改用户名</div>
          <input v-model="newName" placeholder="新用户名" autocomplete="off" />
          <button class="btn sm" :disabled="nameBusy || !newName || newName.trim() === props.user.username">
            {{ nameBusy ? '提交中…' : '修改用户名' }}
          </button>
          <p v-if="nameErr" class="err" style="margin:0">{{ nameErr }}</p>
        </form>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <form @submit.prevent="doPwd" style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🔑 重置密码</div>
          <input v-model="newPwd" type="text" placeholder="新密码（至少 8 位）" autocomplete="off" />
          <button class="btn sm" :disabled="pwdBusy || !newPwd">
            {{ pwdBusy ? '提交中…' : '重置密码' }}
          </button>
          <p style="font-size:12px;color:var(--text-3);margin:0">
            重置后该成员的所有设备将登出，需要用新密码重新登录。把新密码通过其他渠道告诉 TA。
          </p>
          <p v-if="pwdErr" class="err" style="margin:0">{{ pwdErr }}</p>
        </form>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🛡️ 两步验证</div>
          <p style="font-size:12px;color:var(--text-3);margin:0">
            {{ props.user.totp_enabled
              ? '已启用：成员登录需要认证器动态码。成员认证器丢失（无法自助关闭）时，可在此解除——不动密码、不登出已登录设备。'
              : '未启用。' }}
          </p>
          <div v-if="props.user.totp_enabled">
            <button class="btn danger sm" :disabled="totpBusy" @click="clearTotp">
              {{ totpBusy ? '提交中…' : '解除两步验证' }}
            </button>
          </div>
        </div>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🗄️ WebDAV 独立密码</div>
          <p style="font-size:12px;color:var(--text-3);margin:0">
            {{ props.user.webdav_set
              ? '已设置：该成员的 WebDAV 只认独立密码，登录密码无法挂载。只影响 WebDAV，不登出网页会话。'
              : '未设置：WebDAV 目前用登录密码挂载。' }}
          </p>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <input v-model="davPwd" type="text" placeholder="WebDAV 新密码（至少 8 位）" autocomplete="off" style="flex:1;min-width:170px" />
            <button class="btn ghost sm" @click="genDavPwd">🎲 随机</button>
            <button class="btn sm" :disabled="davBusy || davPwd.length < 8" @click="saveDav">保存</button>
            <button v-if="props.user.webdav_set" class="btn danger sm" :disabled="davBusy" @click="clearDav">清除</button>
          </div>
        </div>

        <div class="actions"><button class="btn ghost sm" @click="emit('close')">关闭</button></div>
      </div>
    </div>
  </teleport>
</template>
