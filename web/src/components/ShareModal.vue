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

async function create() {
  busy.value = true
  try {
    const r = await api.createShare(props.paths[0], hours.value, password.value || null, maxDl.value)
    link.value = api.shareLink(r.token)
    await navigator.clipboard?.writeText(link.value).catch(() => {})
    toast('分享链接已复制到剪贴板', 'ok')
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
      <div style="display:flex;gap:10px;justify-content:center;margin-top:14px">
        <img :src="`/api/share/${link.split('/').pop()}/qr`" alt="QR" style="width:150px;height:150px;border-radius:10px" />
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
