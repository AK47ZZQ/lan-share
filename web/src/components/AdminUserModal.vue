<script setup lang="ts">
import { ref } from 'vue'
import { api, type AdminUser } from '../api'
import { toast } from '../ui'
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

        <div class="actions"><button class="btn ghost sm" @click="emit('close')">关闭</button></div>
      </div>
    </div>
  </teleport>
</template>
