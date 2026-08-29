<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type ShareItem, type ShareHistoryRow } from '../api'
import { fmtTs, toast } from '../ui'
import Modal from '../components/Modal.vue'

const items = ref<ShareItem[]>([])
const err = ref('')
const filter = ref<'all' | 'active' | 'done'>('all')
const qrFor = ref<ShareItem | null>(null)
const qrFailed = ref(false)
const historyFor = ref<{ s: ShareItem; rows: ShareHistoryRow[] } | null>(null)
const publicBase = ref('')
function linkOf(s: ShareItem, usePublic = false) {
  return usePublic && publicBase.value ? publicBase.value + '/s/' + s.token : `${location.origin}/s/${s.token}`
}

type Status = 'active' | 'done' | 'expired'
function statusOf(s: ShareItem): Status {
  if (s.max_downloads && s.downloads >= s.max_downloads) return 'done'
  const m = s.expires_in.match(/^(\d+)h/)
  if (m && parseInt(m[1]) <= 0) return 'expired'
  return 'active'
}
const filtered = computed(() => {
  if (filter.value === 'all') return items.value
  return items.value.filter(s => statusOf(s) === filter.value)
})
const counts = computed(() => {
  const c = { all: items.value.length, active: 0, done: 0, expired: 0 }
  for (const s of items.value) c[statusOf(s)]++
  return c
})

async function load() {
  try { items.value = await api.shares() } catch (e) { err.value = (e as Error).message }
}
async function copy(s: ShareItem) {
  const text = s.password ? `${linkOf(s)}
提取码：${s.password}` : linkOf(s)
  try { await navigator.clipboard.writeText(text); toast(s.password ? '已复制链接和提取码' : '链接已复制', 'ok') }
  catch { prompt('请手动复制：', text) }
}
async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); toast('已复制', 'ok') }
  catch { prompt('请手动复制：', text) }
}
async function copyPwd(s: ShareItem) {
  if (!s.password) return
  try { await navigator.clipboard.writeText(s.password); toast('提取码已复制', 'ok') }
  catch { prompt('提取码：', s.password) }
}
async function revoke(s: ShareItem) {
  if (!confirm(`撤销「${s.name}」的分享？链接立即失效。`)) return
  try { await api.revokeShare(s.token); await load(); toast('已撤销', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}
async function showHistory(s: ShareItem) {
  try { historyFor.value = { s, rows: await api.shareHistory(s.token) } }
  catch (e) { toast((e as Error).message, 'err') }
}
const STATUS_LABEL: Record<Status, string> = { active: '有效', done: '已用完', expired: '已过期' }

onMounted(async () => {
  await load()
  try { publicBase.value = (await api.getPublicUrl()).public_base_url } catch { /* 成员不可见 */ }
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>分享管理</h1>
      <div class="view-toggle">
        <button :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ counts.all }}</button>
        <button :class="{ on: filter === 'active' }" @click="filter = 'active'">有效 {{ counts.active }}</button>
        <button :class="{ on: filter === 'done' }" @click="filter = 'done'">已用完 {{ counts.done }}</button>
      </div>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div class="flist" style="display:grid;gap:10px">
      <div v-if="!filtered.length" class="card empty-state">
        <div class="art">🔗</div>
        <div>{{ items.length ? '该状态下没有分享' : '还没有创建过分享' }}</div>
        <div class="hint">在文件页选中文件 → ⋯ 或右键 → 分享</div>
      </div>

      <div v-for="s in filtered" :key="s.token" class="card share-card"
           :class="{ done: statusOf(s) === 'done', expired: statusOf(s) === 'expired' }" style="padding:14px 16px">
        <div class="top">
          <span class="icon" style="width:38px;height:38px;border-radius:10px;display:grid;place-items:center;font-size:17px;background:var(--glass-strong);border:1px solid var(--line);flex:none">
            {{ s.has_password ? '🔒' : '🔗' }}
          </span>
          <span class="sname" :title="s.name">{{ s.name }}</span>
          <span v-if="s.password" class="status-pill" style="cursor:pointer"
                :title="'点击复制提取码'" @click="copyPwd(s)">提取码 {{ s.password }}</span>
          <span class="status-pill" :class="statusOf(s)">{{ STATUS_LABEL[statusOf(s)] }}</span>
        </div>
        <div class="spath" :title="s.path">{{ s.path }} · {{ s.size_human }} · {{ s.expires_in }}内有效
          <template v-if="s.owner && s.owner !== '我'"> · {{ s.owner }}</template></div>
        <div class="bottom">
          <span class="dl-bar" v-if="s.max_downloads">
            <span style="font-size:12px;color:var(--text-3)">已下载 {{ s.downloads }}/{{ s.max_downloads }} 次</span>
            <div class="bar"><i :style="{ width: Math.min(100, Math.round(s.downloads / s.max_downloads * 100)) + '%' }" /></div>
          </span>
          <span v-else style="font-size:12px;color:var(--text-3)">已下载 {{ s.downloads }} 次 · 不限次数</span>
          <span style="display:flex;gap:6px;margin-left:auto;flex-wrap:wrap">
            <button class="btn ghost sm" @click="copy(s)">{{ s.password ? '📋 链接+提取码' : '📋 局域网链接' }}</button>
            <button v-if="publicBase" class="btn ghost sm" @click="copyText(publicBase + '/s/' + s.token + (s.password ? ' | 提取码：' + s.password : ''))">🌐 公网</button>
            <button class="btn ghost sm" @click="qrFor = s; qrFailed = false">🔳 二维码</button>
            <button class="btn ghost sm" @click="showHistory(s)">取件记录</button>
            <button class="iconbtn danger" title="撤销分享" @click="revoke(s)">✕</button>
          </span>
        </div>
      </div>
    </div>

    <!-- 二维码 -->
    <teleport to="body">
      <div v-if="qrFor" class="overlay" @click.self="qrFor = null">
        <div class="modal-card" style="text-align:center">
          <h3>扫码取件 — {{ qrFor.name }}</h3>
          <img v-if="!qrFailed" :src="`/api/share/${qrFor.token}/qr`" alt="二维码"
               style="width:190px;height:190px;background:#fff;padding:8px;border-radius:12px" @error="qrFailed = true" />
          <p v-else style="color:var(--text-3);font-size:13px">二维码生成失败，请使用复制链接</p>
          <input :value="linkOf(qrFor)" readonly style="width:100%;margin-top:8px" @focus="($event.target as HTMLInputElement).select()" />
          <div class="actions"><button class="btn sm" @click="qrFor = null">关闭</button></div>
        </div>
      </div>
    </teleport>

    <!-- 取件记录 -->
    <Modal v-if="historyFor" :title="`取件记录 — ${historyFor.s.name}`" wide @close="historyFor = null">
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
