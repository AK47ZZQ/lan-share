<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, type Member } from '../api'
import { promptDlg, toast } from '../ui'

const props = defineProps<{ paths: string[] }>()
const emit = defineEmits<{ close: [] }>()

const hours = ref(72)
const password = ref('')
const maxDl = ref<number | null>(null)
const targetUser = ref('')
const members = ref<Member[]>([])
const link = ref('')
const busy = ref(false)
const publicBase = ref('')
const qrFailed = ref(false)

onMounted(() => {
  api.members().then(m => members.value = m).catch(() => { /* 名单拉不到则隐藏定向分享 */ })
})
async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); } catch { await promptDlg('请手动复制：', text) }
}
async function copyLink() {
  try {
    await navigator.clipboard.writeText(link.value)
    toast('已复制到剪贴板', 'ok')
  } catch {
    await promptDlg('请手动复制：', link.value)
  }
}

async function create() {
  busy.value = true
  try { publicBase.value = (await api.getPublicUrl()).public_base_url } catch { /* 非管理员的旧分享不受影响 */ }
  try {
    const r = await api.createShare(props.paths[0], hours.value, password.value || null, maxDl.value, targetUser.value || null)
    link.value = api.shareLink(r.token)
    if (targetUser.value) {
      toast(`已分享给「${targetUser.value}」，对方会在铃铛通知里看到`, 'ok')
      emit('close')
      return
    }
    copyText(link.value)
  } catch (e) {
    toast((e as Error).message, 'err')
  } finally { busy.value = false }
}
</script>

<template>
  <div>
    <div v-if="link" class="share-done">
      <p style="font-size:13.5px;color:var(--text-2)">链接已生成：</p>
      <input :value="link" readonly style="width:100%" @focus="($event.target as HTMLInputElement).select()" />
      <div style="display:flex;gap:8px;justify-content:center;margin-top:10px;flex-wrap:wrap">
        <button class="btn ghost sm" @click="copyText(link)">📋 局域网链接</button>
        <button v-if="publicBase" class="btn ghost sm" @click="copyText(publicBase + '/s/' + link.split('/').pop())">🌐 公网链接</button>
      </div>
      <div style="display:flex;gap:10px;justify-content:center;margin-top:14px">
        <img v-if="!qrFailed" :src="`/api/share/${link.split('/').pop()}/qr`" alt="二维码"
             style="width:150px;height:150px;border-radius:10px;background:#fff;padding:6px" @error="qrFailed = true" />
        <p v-else style="font-size:12.5px;color:var(--text-3)">二维码生成失败，可用复制链接分享</p>
      </div>
      <div class="actions"><button class="btn sm" @click="emit('close')">完成</button></div>
    </div>
    <div v-else style="display:grid;gap:12px">
      <p style="font-size:13.5px;color:var(--text-2);margin:0">
        分享 <b>{{ props.paths[0] }}</b>（多人长期协作请在管理面板授权成员）
      </p>
      <label v-if="members.length" class="field">分享给指定成员（可选，站内通知）
        <select v-model="targetUser">
          <option value="">不指定（公开链接）</option>
          <option v-for="m in members" :key="m.id" :value="m.username">{{ m.username }}</option>
        </select>
      </label>
      <p v-if="targetUser" style="font-size:12.5px;color:var(--text-3);margin:0">
        🎯 对方登录后即可直接取件（免提取码），也会收到站内通知；对外链接将不可用。
      </p>
      <template v-if="!targetUser">
        <label class="field">有效期（小时）<input v-model.number="hours" type="number" min="1" max="8760" /></label>
        <label class="field">提取码（可选）<input v-model="password" placeholder="留空则无需提取码" maxlength="32" /></label>
        <label class="field">下载次数上限（可选）<input v-model.number="maxDl" type="number" min="1" placeholder="不限" /></label>
      </template>
      <div class="actions"><button class="btn" :disabled="busy" @click="create">{{ busy ? '创建中…' : targetUser ? '分享给 TA' : '生成链接' }}</button></div>
    </div>
  </div>
</template>
