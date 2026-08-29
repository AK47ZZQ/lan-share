<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ShareItem, type ShareHistoryRow } from '../api'
import { fmtTs, toast } from '../ui'
import Modal from '../components/Modal.vue'

const items = ref<ShareItem[]>([])
const err = ref('')
const qrFor = ref<ShareItem | null>(null)
const qrFailed = ref(false)
const historyFor = ref<{ s: ShareItem; rows: ShareHistoryRow[] } | null>(null)

async function load() {
  try { items.value = await api.shares() } catch (e) { err.value = (e as Error).message }
}
function linkOf(s: ShareItem) { return `${location.origin}/s/${s.token}` }
async function copy(s: ShareItem) {
  try { await navigator.clipboard.writeText(linkOf(s)); toast('链接已复制', 'ok') }
  catch { prompt('请手动复制：', linkOf(s)) }
}
async function revoke(s: ShareItem) {
  if (!confirm(`撤销「${s.name}」的分享？链接立即失效。`)) return
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
      <h1>分享管理</h1>
      <span class="badge" v-if="items.length">{{ items.length }} 个分享</span>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div class="card flist" style="padding:8px">
      <div v-if="!items.length" class="empty-state">
        <div class="art">🔗</div>
        <div>还没有创建过分享</div>
        <div class="hint">在文件页选中文件 → ⋯ 或右键 → 分享</div>
      </div>
      <div v-for="s in items" :key="s.token" class="frow" style="cursor:default">
        <span class="icon">{{ s.has_password ? '🔒' : '🔗' }}</span>
        <span class="name">
          <div>{{ s.name }} <span class="badge" v-if="s.max_downloads">{{ s.downloads }}/{{ s.max_downloads }} 次</span>
            <span class="badge" v-if="s.has_password">提取码</span></div>
          <small>{{ s.path }} · {{ s.expires_in }}内有效<template v-if="s.owner"> · {{ s.owner }}</template></small>
        </span>
        <span class="meta">{{ s.size_human }}</span>
        <span class="ops" style="opacity:1">
          <button class="btn ghost sm" @click="copy(s)">复制</button>
          <button class="btn ghost sm" @click="qrFor = s; qrFailed = false">二维码</button>
          <button class="btn ghost sm" @click="showHistory(s)">取件记录</button>
          <button class="iconbtn danger" title="撤销分享" @click="revoke(s)">✕</button>
        </span>
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
