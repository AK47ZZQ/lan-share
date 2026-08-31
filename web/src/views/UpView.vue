<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { me, refreshAuth } from '../auth'
import { toast } from '../ui'
import { uploadFiles, uploadingNow } from '../uploader'
import UploadQueue from '../components/UploadQueue.vue'

const props = defineProps<{ me?: { username: string; role: string } }>()
const checking = ref(true)
const busy = uploadingNow

onMounted(async () => {
  if (!props.me) await refreshAuth()
  checking.value = false
})

async function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (!input.files?.length) return
  await uploadFiles(Array.from(input.files), {
    destDir: 'incoming',
    onEachDone: n => toast(`${n} 已备份`, 'ok'),
    onEachFail: n => toast(`${n} 上传失败`, 'err'),
  })
  input.value = ''
}
</script>

<template>
  <main class="page" style="max-width:560px">
    <div v-if="checking" class="card empty">检查登录态…</div>
    <div v-else-if="!me" class="card" style="padding:30px;text-align:center">
      <p>请先登录后使用直传（文件将落到你的 incoming 目录）。</p>
      <a class="btn" href="/login?next=/up">去登录</a>
    </div>
    <template v-else>
      <div class="card" style="padding:28px;text-align:center">
        <div style="font-size:40px;margin-bottom:6px">📲</div>
        <h2 style="margin:0 0 4px">手机直传</h2>
        <p style="color:var(--text-3);font-size:13.5px;margin:0 0 18px">
          {{ me.username }} · 文件将备份到你的 incoming 目录
        </p>
        <label class="btn" style="font-size:16px;padding:13px 26px;position:relative">
          {{ busy ? '备份中…' : '选择照片 / 文件' }}
          <input type="file" multiple hidden accept="*/*" @change="onPick" />
        </label>
      </div>
      <UploadQueue dest-dir="incoming" />
      <p style="text-align:center;margin-top:16px">
        <a href="/" style="font-size:13px;color:var(--text-3)">打开完整网盘 →</a>
      </p>
    </template>
  </main>
</template>
