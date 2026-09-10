<script setup lang="ts">
// 全量分享一览（v3.45，从 v3.39 候选清单落地）：admin 视角的全员分享管理页。
// 数据复用 GET /api/shares（admin 调用即全量、带 owner），不新增服务端端点；
// 风格沿 notify-ops 既有 card + frow 表格语言。
// 注：提取码明文自 R1-P2-5 起不再随接口下发（一次性下发不落库的既定安全
// 决策），本页以"带提取码"徽章呈现 has_password；要恢复明文可见需主人拍板。
import { computed, onMounted, ref } from 'vue'
import { api, type ShareHistoryRow, type ShareItem } from '../api'
import { confirmDlg, fmtTs, promptDlg, toast } from '../ui'
import Modal from '../components/Modal.vue'

const items = ref<ShareItem[]>([])
const err = ref('')
const busy = ref(false)
const ownerFilter = ref('')
const statusFilter = ref<'all' | 'active' | 'done' | 'expired'>('all')
const historyFor = ref<{ s: ShareItem; rows: ShareHistoryRow[] } | null>(null)

type Status = 'active' | 'done' | 'expired'
function statusOf(s: ShareItem): Status {
  if (s.max_downloads && s.downloads >= s.max_downloads) return 'done'
  const m = s.expires_in.match(/^(\d+)h/)
  if (m && parseInt(m[1]) <= 0) return 'expired'
  return 'active'
}
const STATUS_LABEL: Record<Status, string> = { active: '有效', done: '已用完', expired: '已过期' }

const owners = computed(() => [...new Set(items.value.map(s => s.owner))].sort())
const filtered = computed(() => items.value.filter(s =>
  (!ownerFilter.value || s.owner === ownerFilter.value) &&
  (statusFilter.value === 'all' || statusOf(s) === statusFilter.value)))
const counts = computed(() => {
  const c = { all: items.value.length, active: 0, done: 0, expired: 0 }
  for (const s of items.value) c[statusOf(s)]++
  return c
})

async function load() {
  busy.value = true
  try { items.value = await api.shares(); err.value = '' }
  catch (e) { err.value = (e as Error).message } finally { busy.value = false }
}
async function copyLink(s: ShareItem) {
  const link = `${location.origin}/s/${s.token}`
  try { await navigator.clipboard.writeText(link); toast('链接已复制', 'ok') }
  catch { await promptDlg('请手动复制：', link) }
}
async function revoke(s: ShareItem) {
  if (!(await confirmDlg(`撤销「${s.owner}」分享的「${s.name}」？链接立即失效。`, { danger: true, okText: '撤销' }))) return
  try { await api.revokeShare(s.token); await load(); toast('已撤销', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}
async function showHistory(s: ShareItem) {
  try { historyFor.value = { s, rows: await api.shareHistory(s.token) } }
  catch (e) { toast((e as Error).message, 'err') }
}

onMounted(load)
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>全部分享</h1>
      <span style="font-size:12.5px;color:var(--text-3)">全员分享一览 · 撤销与取件记录</span>
      <div class="view-toggle">
        <button :class="{ on: statusFilter === 'all' }" @click="statusFilter = 'all'">全部 {{ counts.all }}</button>
        <button :class="{ on: statusFilter === 'active' }" @click="statusFilter = 'active'">有效 {{ counts.active }}</button>
        <button :class="{ on: statusFilter === 'done' }" @click="statusFilter = 'done'">已用完 {{ counts.done }}</button>
        <button :class="{ on: statusFilter === 'expired' }" @click="statusFilter = 'expired'">已过期 {{ counts.expired }}</button>
      </div>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div class="card" style="padding:14px 16px;display:grid;gap:10px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <div style="font-size:13.5px;font-weight:600">🔗 分享列表</div>
        <span style="font-size:12px;color:var(--text-3)">共 {{ items.length }} 条 · 按创建者/状态筛选</span>
        <div style="flex:1" />
        <select v-model="ownerFilter" style="font-size:13px">
          <option value="">全部创建者</option>
          <option v-for="o in owners" :key="o" :value="o">{{ o }}</option>
        </select>
        <button class="btn ghost sm" :disabled="busy" @click="load">刷新</button>
      </div>

      <div v-if="!filtered.length && !busy" class="empty">{{ items.length ? '该筛选条件下没有分享' : '当前没有任何分享' }}</div>
      <div v-for="s in filtered" :key="s.token" class="frow" style="cursor:default;align-items:flex-start">
        <span class="icon">{{ s.has_password ? '🔒' : '🔗' }}</span>
        <span class="name" style="flex:1">
          <div style="font-size:13.5px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            <b>{{ s.name }}</b>
            <span class="badge" :style="statusOf(s) === 'active' ? 'color:#2e9e5b' : ''">{{ STATUS_LABEL[statusOf(s)] }}</span>
            <span v-if="s.has_password" class="badge">带提取码</span>
            <span v-if="s.target" class="badge" title="定向分享：仅该成员可取件">🎯 {{ s.target }}</span>
          </div>
          <div style="font-size:12px;color:var(--text-3)" :title="s.path">{{ s.path }} · {{ s.size_human }}</div>
          <small>{{ s.owner }} · 创建于 {{ fmtTs(s.created_at) }} · {{ s.expires_in }}内有效 · 已取件 {{ s.downloads }}{{ s.max_downloads ? `/${s.max_downloads}` : ' 次（不限）' }}</small>
        </span>
        <span class="ops" style="opacity:1">
          <button class="btn ghost sm" @click="copyLink(s)">📋 链接</button>
          <button class="btn ghost sm" @click="showHistory(s)">取件记录</button>
          <button class="btn danger sm" @click="revoke(s)">✕ 撤销</button>
        </span>
      </div>
    </div>

    <!-- 取件记录 -->
    <Modal v-if="historyFor" :title="`取件记录 — ${historyFor.s.name}（${historyFor.s.owner}）`" wide @close="historyFor = null">
      <div v-if="!historyFor.rows.length" class="empty">还没有取件记录</div>
      <div v-for="(r, i) in historyFor.rows" :key="i" class="frow" style="cursor:default">
        <span class="icon">{{ r.action === 'share_download' ? '📥' : '⚠️' }}</span>
        <span class="name">
          <div>{{ r.action === 'share_download' ? '下载成功' : '密码错误' }}</div>
          <small>{{ r.ip }}</small>
        </span>
        <span class="meta">{{ fmtTs(r.ts) }}</span>
      </div>
    </Modal>
  </main>
</template>
