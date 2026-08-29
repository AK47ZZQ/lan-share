<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type DiskInfo, type Me } from '../api'
import { human } from '../ui'

const props = defineProps<{ me: Me }>()
const emit = defineEmits<{ close: [] }>()

const loading = ref(true)
const err = ref('')
const disk = ref<DiskInfo | null>(null)
const detail = ref<{ share_size_human: string; categories: Record<string, { size: number; size_human: string }> | null; private_used: number | null; quota_bytes: number | null } | null>(null)

onMounted(async () => {
  try {
    disk.value = await api.disk()
    try { detail.value = await fetch('/api/disk/detail').then(r => r.ok ? r.json() : null) } catch { /* */ }
  } catch (e) { err.value = (e as Error).message }
  loading.value = false
})

const usedPct = computed(() => disk.value && disk.value.total_bytes ? Math.round(disk.value.used_bytes / disk.value.total_bytes * 100) : 0)
const quotaPct = computed(() => {
  const d = detail.value
  if (!d?.quota_bytes || d.private_used == null) return 0
  return Math.min(100, Math.round(d.private_used / d.quota_bytes * 100))
})
const cats = computed<[string, { size: number; size_human: string }][]>(() => {
  const c = detail.value?.categories
  if (!c) return []
  return Object.entries(c).sort((a, b) => b[1].size - a[1].size)
})
function barWidth(size: number) {
  const total = detail.value?.categories ? Math.max(...Object.values(detail.value!.categories!).map(x => x.size)) : 1
  return Math.max(3, Math.round(size / Math.max(total, 1) * 100))
}
</script>

<template>
  <teleport to="body">
    <div class="overlay" @click.self="emit('close')">
      <div class="modal-card disk-modal">
        <h3>存储空间</h3>
        <p v-if="err" class="err">{{ err }}</p>
        <div v-if="loading" class="empty">统计中…</div>
        <template v-else-if="disk">
          <div class="sect">
            <div class="sect-row"><span>硬盘用量</span><b>{{ disk.used }} / {{ disk.total }}</b></div>
            <div class="bar big"><i :class="{ hot: usedPct > 85 }" :style="{ width: usedPct + '%' }" /></div>
            <div class="sect-sub">已用 {{ usedPct }}% · 剩余 {{ disk.free }}
              <span v-if="disk.warning" class="badge danger">空间不足</span></div>
          </div>

          <div class="sect" v-if="detail">
            <div class="sect-row"><span>网盘内容合计</span><b>{{ detail.share_size_human }}</b>
              <small class="hint-inline">（硬链接去重后物理占用）</small></div>
          </div>

          <div class="sect" v-if="detail?.categories">
            <div class="sect-row"><span>目录构成</span></div>
            <div v-for="entry in cats" :key="entry[0]" class="cat">
              <div class="sect-row small"><span>{{ entry[0] }}</span><b>{{ entry[1].size_human }}</b></div>
              <div class="bar"><i :style="{ width: barWidth(entry[1].size) + '%' }" /></div>
            </div>
          </div>

          <div class="sect" v-if="detail?.private_used != null">
            <div class="sect-row"><span>我的私人空间</span>
              <b>{{ human(detail.private_used) }}{{ detail.quota_bytes ? ' / ' + human(detail.quota_bytes) : '' }}</b></div>
            <div class="bar big" v-if="detail.quota_bytes">
              <i :class="{ hot: quotaPct > 85 }" :style="{ width: quotaPct + '%' }" />
            </div>
            <div class="sect-sub" v-if="detail.quota_bytes">已用 {{ quotaPct }}%</div>
          </div>

          <div class="actions"><button class="btn sm" @click="emit('close')">关闭</button></div>
        </template>
      </div>
    </div>
  </teleport>
</template>

<style>
.disk-modal { width: min(480px, 94vw); }
.sect { padding: 10px 0; border-bottom: 1px solid var(--line); }
.sect:last-of-type { border-bottom: 0; }
.sect-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 7px; font-size: 14px; }
.sect-row b { margin-left: auto; font-size: 13.5px; }
.sect-row.small { font-size: 13px; margin-bottom: 4px; }
.sect-row.small b { font-size: 12.5px; color: var(--text-2); }
.sect-sub { font-size: 12.5px; color: var(--text-3); margin-top: 5px; display: flex; gap: 6px; align-items: center; }
.hint-inline { color: var(--text-3); font-size: 12px; margin-left: auto; }
.cat { margin-bottom: 8px; }
.bar.big { height: 8px; }
.bar i.hot { background: linear-gradient(135deg, #e5534b, #d64562); }
</style>
