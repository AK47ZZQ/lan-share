<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { confirmLargeUpload, uploadResumable } from '../api'
import { me, refreshAuth } from '../auth'
import { toast } from '../ui'

const props = defineProps<{ me?: { username: string; role: string } }>()
const checking = ref(true)
const uploads = ref<{ name: string; sent: number; total: number; stage: string }[]>([])
const busy = ref(false)

onMounted(async () => {
  if (!props.me) await refreshAuth()
  checking.value = false
})

async function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (!input.files?.length) return
  busy.value = true
  for (const f of Array.from(input.files)) {
    if (!(await confirmLargeUpload(f))) continue  // 公网大文件：用户取消则跳过该文件
    const item = { name: f.name, sent: 0, total: f.size, stage: '上传中' }
    uploads.value.push(item)
    try {
      const r = await uploadResumable('incoming', f, (sent, total, stage) => { item.sent = sent; item.total = total; item.stage = stage })
      item.stage = r.instant ? '秒传完成 ✓' : '完成 ✓'
      toast(`${f.name} 已备份`, 'ok')
    } catch (e) {
      item.stage = '失败：' + (e as Error).message
      toast(`${f.name} 上传失败`, 'err')
    }
  }
  busy.value = false
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
      <div v-if="uploads.length" class="upl card" style="position:static;width:auto;margin-top:14px;padding:12px 16px">
        <div v-for="u in uploads" :key="u.name" class="item">
          <div style="display:flex;justify-content:space-between;gap:10px">
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ u.name }}</span>
            <span style="color:var(--text-3)">{{ u.stage }}</span>
          </div>
          <div class="bar"><i :style="{ width: (u.total ? Math.round(u.sent / u.total * 100) : 0) + '%' }" /></div>
        </div>
      </div>
      <p style="text-align:center;margin-top:16px">
        <a href="/" style="font-size:13px;color:var(--text-3)">打开完整网盘 →</a>
      </p>
    </template>
  </main>
</template>
