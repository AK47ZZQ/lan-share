<script setup lang="ts">
// 运行状态（v3.39 新增）：服务健康探针 / 磁盘 / 版本对账 / 审计日志。
import { onMounted, ref } from 'vue'
import { api, type AuditItem, type ConsoleOverview, type MetricsSnapshot } from '../api'
import { fmtTs, human } from '../ui'

const err = ref('')
const health = ref<{ status: string; db: boolean; data_dir: boolean; disk_free_gb: number; disk_total_gb: number; disk_used_pct: number } | null>(null)
const ov = ref<ConsoleOverview | null>(null)
const metrics = ref<MetricsSnapshot | null>(null)
const audits = ref<AuditItem[]>([])
const auditOpen = ref(false)
const auditBusy = ref(false)
const auditAction = ref('')
const auditActions = ref<string[]>([])

async function loadAll() {
  try { ov.value = await api.adminOverview() } catch (e) { err.value = (e as Error).message }
  // healthz 是免认证探针，这里仅作展示复用
  try { health.value = await fetch('/healthz').then(r => r.json()) } catch { /* */ }
  // 备份卡读取失败不挡其他卡（admin 专属端点，越权场景静默）
  try { metrics.value = await api.getMetrics() } catch { /* */ }
}
// 14 天流量迷你柱状图：以窗口内最大值为满高，全零时也是 2px 的底柱
function barH(b: number): string {
  const days = ov.value?.traffic.days ?? []
  const max = Math.max(1, ...days.map(x => x.bytes))
  return Math.max(2, Math.round(b / max * 40)) + 'px'
}
async function loadAudit() {
  auditOpen.value = true; auditBusy.value = true
  try {
    const r = await api.auditLog(80, auditAction.value)
    audits.value = r.items
    auditActions.value = r.actions   // 全集（不随筛选变化），供下拉
  } catch (e) { toastErr((e as Error).message) } finally { auditBusy.value = false }
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

    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">💾 备份</div>
      <div v-if="metrics" class="status-grid">
        <div class="status-item">兜底备份（服务端每日自动）：
          <template v-if="metrics.backup">
            <span class="badge" :class="metrics.backup.ok ? '' : 'danger'">{{ metrics.backup.ok ? '✓ 成功' : '⚠ 失败' }}</span>
            <span v-if="metrics.backup.ok && metrics.backup.verified === true" class="badge">验证通过</span>
            <span v-else-if="metrics.backup.ok && metrics.backup.verified === false" class="badge danger">验证失败</span>
            <span v-else-if="metrics.backup.ok" class="badge">未校验</span>
            <span style="color:var(--text-3);font-size:12px">{{ fmtTs(metrics.backup.ts) }} · {{ metrics.backup.detail }}{{ metrics.backup.verify_detail ? ' · ' + metrics.backup.verify_detail : '' }}</span>
          </template>
          <template v-else><span class="badge danger">暂无记录</span></template>
        </div>
        <div class="status-item" style="font-size:11.5px;color:var(--text-3)">
          验证 = dump 落盘后立即校验完整性（integrity_check / pg_restore -l）；失败会自动 webhook 告警。
        </div>
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

    <div v-if="ov" class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">🌐 公网流量（隧道）</div>
      <div class="status-grid">
        <div class="status-item">今日：<b>{{ human(ov.traffic.today_bytes) }}</b></div>
        <div class="status-item">
          本月：<b>{{ human(ov.traffic.month_bytes) }}</b>
          <span class="badge" :class="ov.traffic.month_bytes > ov.traffic.free_tier_bytes * 0.8 ? 'danger' : ''">
            免费档 {{ (ov.traffic.month_bytes / ov.traffic.free_tier_bytes * 100).toFixed(1) }}%
          </span>
        </div>
      </div>
      <div class="traffic-days">
        <div v-for="d in ov.traffic.days" :key="d.date" class="traffic-day"
             :title="`${d.date} · ${human(d.bytes)}`">
          <div class="traffic-bar" :style="{ height: barH(d.bytes) }" />
          <div class="traffic-label">{{ d.date.slice(5) }}</div>
        </div>
      </div>
      <div style="font-size:11.5px;color:var(--text-3)">
        口径：Caddy 隧道入口出口字节（浏览/下载为主，公网上传方向以樱花FRP 面板为准）；
        LAN 直连不消耗隧道配额，不计入。
      </div>
    </div>

    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:10px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <div style="font-size:13.5px;font-weight:600">📜 审计日志</div>
        <span style="font-size:12px;color:var(--text-3)">登录/分享/删除/吊销等操作留痕（最近 80 条）</span>
        <div style="flex:1" />
        <select v-model="auditAction" style="font-size:13px" @change="loadAudit">
          <option value="">全部类型</option>
          <option v-for="a in auditActions" :key="a" :value="a">{{ a }}</option>
        </select>
        <button class="btn ghost sm" :disabled="auditBusy" @click="loadAudit">{{ auditOpen ? '刷新' : '查看' }}</button>
      </div>
      <template v-if="auditOpen">
        <div v-if="!audits.length" class="empty">{{ auditAction ? '该类型暂无记录' : '暂无记录' }}</div>
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
.traffic-days { display: flex; gap: 4px; align-items: flex-end; height: 56px; padding: 0 2px; }
.traffic-day { display: flex; flex-direction: column; align-items: center; gap: 3px; flex: 1; min-width: 0; }
.traffic-bar { width: 100%; max-width: 22px; background: linear-gradient(180deg, var(--accent), var(--accent-2)); border-radius: 3px 3px 0 0; }
.traffic-label { font-size: 9.5px; color: var(--text-3); white-space: nowrap; }
</style>
