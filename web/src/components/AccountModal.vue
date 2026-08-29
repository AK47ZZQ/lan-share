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

        <div class="actions"><button class="btn ghost sm" @click="emit('close')">关闭</button></div>
      </div>
    </div>
  </teleport>
</template>
