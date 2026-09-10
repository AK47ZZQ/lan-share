<script setup lang="ts">
// 运行状态（v3.39 新增）：服务健康探针 / 磁盘 / 版本对账 / 审计日志。
import { onMounted, ref } from 'vue'
import { api, type AuditItem, type ConsoleOverview } from '../api'
import { fmtTs, human } from '../ui'

const err = ref('')
const health = ref<{ status: string; db: boolean; data_dir: boolean; disk_free_gb: number; disk_total_gb: number; disk_used_pct: number } | null>(null)
const ov = ref<ConsoleOverview | null>(null)
const audits = ref<AuditItem[]>([])
const auditOpen = ref(false)
const auditBusy = ref(false)

async function loadAll() {
  try { ov.value = await api.adminOverview() } catch (e) { err.value = (e as Error).message }
  // healthz 是免认证探针，这里仅作展示复用
  try { health.value = await fetch('/healthz').then(r => r.json()) } catch { /* */ }
}
async function loadAudit() {
  auditOpen.value = true; auditBusy.value = true
  try { audits.value = (await api.auditLog(80)).items } catch (e) { toastErr((e as Error).message) } finally { auditBusy.value = false }
}
function toastErr(msg: string) { err.value = msg }

onMounted(loadAll)
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>运行状态</h1>
      <button class="btn ghost sm" @click="loadAll">刷新</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div class="card" style="padding:14px 16px;display:grid;gap:10px">
      <div style="font-size:13.5px;font-weight:600">🩺 健康探针（/healthz）</div>
      <div v-if="health" class="status-grid">
        <div class="status-item">
          <span class="badge" :class="health.status === 'ok' ? '' : 'danger'">{{ health.status === 'ok' ? '✓ 正常' : '⚠ 降级' }}</span>
        </div>
        <div class="status-item">数据库：<span class="badge" :class="health.db ? '' : 'danger'">{{ health.db ? '连通' : '失败' }}</span></div>
        <div class="status-item">数据目录：<span class="badge" :class="health.data_dir ? '' : 'danger'">{{ health.data_dir ? '可写' : '不可写' }}</span></div>
        <div class="status-item">磁盘：{{ health.disk_used_pct }}% · 剩 {{ health.disk_free_gb }} GB / {{ health.disk_total_gb }} GB</div>
      </div>
      <div v-else class="empty">读取中…</div>
    </div>

    <div v-if="ov" class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">📦 版本与规模对账</div>
      <div class="status-grid">
        <div class="status-item">App 最新分发：<b>{{ ov.app_latest?.version ?? '—' }}</b>（build {{ ov.app_latest?.build ?? '—' }}）</div>
        <div class="status-item">常规文件规模（治理视图）：{{ human(ov.blobs.size) }} 唯一内容 / {{ ov.blobs.count }} 指纹</div>
        <div class="status-item">照片：{{ ov.photos.count }} 张（{{ human(ov.photos.size) }}）</div>
        <div class="status-item">服务器时间：{{ fmtTs(ov.server_time) }}</div>
      </div>
    </div>

    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:10px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <div style="font-size:13.5px;font-weight:600">📜 审计日志</div>
        <span style="font-size:12px;color:var(--text-3)">登录/分享/删除/吊销等操作留痕（最近 80 条）</span>
        <div style="flex:1" />
        <button class="btn ghost sm" :disabled="auditBusy" @click="loadAudit">{{ auditOpen ? '刷新' : '查看' }}</button>
      </div>
      <template v-if="auditOpen">
        <div v-if="!audits.length" class="empty">暂无记录</div>
        <div v-for="(a, i) in audits" :key="i" class="act-row">
          <span class="act-time">{{ fmtTs(a.ts) }}</span>
          <span class="act-text">
            <b>{{ a.username || '匿名' }}</b>
            <span class="badge">{{ a.action }}</span>
            <span style="color:var(--text-3)">{{ a.target }}{{ a.detail ? ' · ' + a.detail : '' }}</span>
            <span style="color:var(--text-3);font-size:11px">{{ a.ip }}</span>
          </span>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.status-grid { display: flex; gap: 18px; flex-wrap: wrap; font-size: 13.5px; align-items: center; }
.status-item { display: flex; gap: 6px; align-items: center; }
.act-row {
  display: flex; gap: 10px; align-items: baseline;
  padding: 6px 2px; border-top: 1px solid var(--line); font-size: 13px;
}
.act-row:first-of-type { border-top: 0; }
.act-text { flex: 1; min-width: 0; }
.act-time { font-size: 11.5px; color: var(--text-3); white-space: nowrap; }
</style>
