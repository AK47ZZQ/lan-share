<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import { toast } from '../ui'

const props = defineProps<{ paths: string[] }>()
const emit = defineEmits<{ close: [] }>()

const hours = ref(72)
const password = ref('')
const maxDl = ref<number | null>(null)
const link = ref('')
const busy = ref(false)
const publicBase = ref('')
const qrFailed = ref(false)
async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); } catch { prompt('请手动复制：', text) }
}
async function copyLink() {
  try {
    await navigator.clipboard.writeText(link.value)
    toast('已复制到剪贴板', 'ok')
  } catch {
    prompt('请手动复制：', link.value)
  }
}

async function create() {
  busy.value = true
  try { publicBase.value = (await api.getPublicUrl()).public_base_url } catch { /* 非管理员的旧分享不受影响 */ }
  try {
    const r = await api.createShare(props.paths[0], hours.value, password.value || null, maxDl.value)
    link.value = api.shareLink(r.token)
    copyText(link.value)
    toast('局域网链接已复制' + (publicBase.value ? '（公网链接可用下方按钮）' : ''), 'ok')
  } catch (e) {
    toast((e as Error).message, 'err')
  } finally { busy.value = false }
}
</script>

<template>
  <div>
    <p v-if="!link" style="font-size:13.5px;color:var(--text-2);margin:0 0 12px">
      分享 <b>{{ props.paths[0] }}</b>（多人分享请在管理面板授权成员）
    </p>
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
      <label class="field">有效期（小时）<input v-model.number="hours" type="number" min="1" max="8760" /></label>
      <label class="field">提取码（可选）<input v-model="password" placeholder="留空则无需提取码" maxlength="32" /></label>
      <label class="field">下载次数上限（可选）<input v-model.number="maxDl" type="number" min="1" placeholder="不限" /></label>
      <div class="actions"><button class="btn" :disabled="busy" @click="create">{{ busy ? '创建中…' : '生成链接' }}</button></div>
    </div>
  </div>
</template>
