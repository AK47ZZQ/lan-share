<script setup lang="ts">
// 控制台总览（v3.39 定位更新）：一屏看全站——存储/成员/照片/分享/回收站/
// 秒传库/会话设备/App 版本 + 最近动态。全部只读聚合（/api/admin/overview）。
import { computed, onMounted, ref } from 'vue'
import { api, type ConsoleOverview, type ActivityItem } from '../api'
import { ACTION_NAMES, fmtTs, human } from '../ui'

const ov = ref<ConsoleOverview | null>(null)
const acts = ref<ActivityItem[]>([])
const err = ref('')

const diskBarStyle = computed(() => {
  const pct = ov.value?.disk.used_pct ?? 0
  const color = pct >= 85 ? 'var(--danger, #d33)' : pct >= 70 ? '#b8860b' : 'var(--accent, #4f7cff)'
  return { width: `${Math.min(pct, 100)}%`, background: color }
})

onMounted(async () => {
  try {
    const [o, acts_] = await Promise.all([api.adminOverview(), api.activity(40)])
    ov.value = o
    acts.value = acts_.slice(0, 8)
  } catch (e) { err.value = (e as Error).message }
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>总览</h1>
      <span style="font-size:12.5px;color:var(--text-3)">
        站长工作台 · 数据截至 {{ ov ? fmtTs(ov.server_time) : '加载中…' }}</span>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <template v-if="ov">
      <div class="stat-grid">
        <!-- 存储 -->
        <div class="card stat-card stat-wide">
          <div class="stat-title">💾 存储空间</div>
          <div class="stat-big">{{ ov.disk.used_pct }}%</div>
          <div class="stat-bar"><i :style="diskBarStyle" /></div>
          <div class="stat-sub">已用 {{ human(ov.disk.used_bytes) }} / {{ human(ov.disk.total_bytes) }}
            · 剩余 {{ human(ov.disk.free_bytes) }}</div>
        </div>
        <!-- 成员 -->
        <div class="card stat-card">
          <div class="stat-title">👥 成员</div>
          <div class="stat-big">{{ ov.users.total }}</div>
          <div class="stat-sub">活跃 {{ ov.users.active }} · 管理员 {{ ov.users.admins }} · 访客 {{ ov.users.guests }}</div>
          <router-link to="/admin" class="stat-link">成员与权限 →</router-link>
        </div>
        <!-- 照片 -->
        <div class="card stat-card">
          <div class="stat-title">🖼️ 照片库</div>
          <div class="stat-big">{{ ov.photos.count }}</div>
          <div class="stat-sub">共 {{ human(ov.photos.size) }}</div>
          <router-link to="/photos" class="stat-link">去照片墙 →</router-link>
        </div>
        <!-- 分享 -->
        <div class="card stat-card">
          <div class="stat-title">🔗 分享</div>
          <div class="stat-big">{{ ov.shares.active }}</div>
          <div class="stat-sub">有效 / 累计 {{ ov.shares.total }} · 累计下载 {{ ov.shares.downloads }} 次</div>
          <router-link to="/shares" class="stat-link">我的分享 →</router-link>
        </div>
        <!-- 回收站 -->
        <div class="card stat-card">
          <div class="stat-title">🗑️ 回收站</div>
          <div class="stat-big">{{ ov.trash.count }}</div>
          <div class="stat-sub">占用 {{ human(ov.trash.size) }}</div>
          <router-link to="/trash" class="stat-link">去回收站 →</router-link>
        </div>
        <!-- 秒传库 -->
        <div class="card stat-card">
          <div class="stat-title">⚡ 秒传指纹库</div>
          <div class="stat-big">{{ ov.blobs.count }}</div>
          <div class="stat-sub">唯一内容 {{ human(ov.blobs.size) }}</div>
        </div>
        <!-- 会话与设备 -->
        <div class="card stat-card">
          <div class="stat-title">📡 会话 / 设备</div>
          <div class="stat-big">{{ ov.sessions.active }} <span style="font-size:15px;color:var(--text-3)">/ {{ ov.devices.active }}</span></div>
          <div class="stat-sub">Web 会话 / App 设备（活跃）</div>
          <router-link to="/governance" class="stat-link">治理工具 →</router-link>
        </div>
        <!-- App 版本 -->
        <div class="card stat-card">
          <div class="stat-title">📱 App 最新版</div>
          <div class="stat-big">{{ ov.app_latest?.version ?? '—' }}</div>
          <div class="stat-sub">{{ ov.app_latest ? `build ${ov.app_latest.build} · 成员「检查更新」可升级` : '尚未配置分发' }}</div>
        </div>
      </div>

      <!-- 最近动态 -->
      <div class="card" style="margin-top:14px;padding:12px 14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <div style="font-size:13.5px;font-weight:600">🔔 最近动态</div>
          <div style="flex:1" />
          <router-link to="/activity" class="stat-link">全部动态 →</router-link>
        </div>
        <div v-if="!acts.length" class="empty">还没有动态</div>
        <div v-for="(a, i) in acts" :key="i" class="act-row">
          <span class="act-dot" />
          <span class="act-text"><b>{{ a.username }}</b> {{ ACTION_NAMES[a.action] || a.action }}
            <span style="color:var(--text-3)">{{ a.target }}</span>
            <span v-if="a.count > 1" class="badge">×{{ a.count }}</span></span>
          <span class="act-time">{{ fmtTs(a.ts) }}</span>
        </div>
      </div>
    </template>
  </main>
</template>

<style scoped>
.stat-grid {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(215px, 1fr));
}
.stat-card { padding: 13px 15px; display: grid; gap: 4px; align-content: start; }
.stat-wide { grid-column: span 2; }
@media (max-width: 640px) { .stat-wide { grid-column: span 1; } }
.stat-title { font-size: 13px; font-weight: 600; color: var(--text-2); }
.stat-big { font-size: 26px; font-weight: 700; line-height: 1.15; }
.stat-bar { height: 7px; border-radius: 99px; background: var(--hover); overflow: hidden; margin: 3px 0 2px; }
.stat-bar i { display: block; height: 100%; border-radius: 99px; }
.stat-sub { font-size: 12px; color: var(--text-3); }
.stat-link { font-size: 12px; color: var(--accent); text-decoration: none; margin-top: 2px; }
.act-row {
  display: flex; gap: 9px; align-items: baseline;
  padding: 6px 2px; border-top: 1px solid var(--line); font-size: 13.5px;
}
.act-row:first-of-type { border-top: 0; }
.act-dot { width: 6px; height: 6px; border-radius: 99px; background: var(--accent); flex-shrink: 0; align-self: center; }
.act-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.act-time { font-size: 11.5px; color: var(--text-3); white-space: nowrap; }
</style>
