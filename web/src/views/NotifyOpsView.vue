<script setup lang="ts">
// 通知运营（v3.39 从 AdminView 拆出）：手动广播 + 发送记录/撤回/提醒未读/草稿定时。
import { onMounted, ref } from 'vue'
import { api, type AdminUser, type BroadcastItem } from '../api'
import { confirmDlg, toast } from '../ui'

const err = ref('')
const users = ref<AdminUser[]>([])

// 手动广播（v0.20）：标题/正文 + 可选成员，站内通知 + webhook 一起推
const notifyOpen = ref(false)
const notifyTitle = ref('')
const notifyBody = ref('')
const notifyTargets = ref<number[]>([])
const notifyBusy = ref(false)
const notifyOk = ref(false)
async function sendNotify() {
  if (!notifyTitle.value.trim()) return
  notifyBusy.value = true; notifyOk.value = false
  try {
    const r = await api.adminNotify(notifyTitle.value.trim(), notifyBody.value.trim(),
      notifyTargets.value.length ? [...notifyTargets.value] : undefined)
    toast(`已推送给 ${r.sent} 人${r.webhook ? '，站外 webhook 同步发送' : ''}`, 'ok')
    notifyOk.value = true
    setTimeout(() => { notifyOk.value = false; notifyOpen.value = false }, 1200)
    notifyTitle.value = ''; notifyBody.value = ''; notifyTargets.value = []
  } catch (e) { toast((e as Error).message, 'err') } finally { notifyBusy.value = false }
}
function toggleNotify(id: number) {
  notifyTargets.value = notifyTargets.value.includes(id)
    ? notifyTargets.value.filter(x => x !== id)
    : [...notifyTargets.value, id]
}

// 通知管理（9.6-P2-14）：发送记录/撤回/提醒未读/草稿定时
const broadcasts = ref<BroadcastItem[]>([])
const bcOpen = ref(false)
const bcBusy = ref(false)
const draftTitle = ref('')
const draftBody = ref('')
const draftTarget = ref<'all' | 'pick'>('all')
const draftTime = ref('')  // HH:mm，非空 = 定时
async function loadBroadcasts() {
  bcOpen.value = true; bcBusy.value = true
  try {
    broadcasts.value = (await api.sentBroadcasts()).items
  } catch (e) { toast((e as Error).message, 'err') } finally { bcBusy.value = false }
}
async function withdraw(b: BroadcastItem) {
  if (!(await confirmDlg(`撤回「${b.title}」的所有未读通知？已读的保留为历史。`, { title: '撤回' }))) return
  bcBusy.value = true
  try {
    const r = await api.withdrawBroadcast(b.id)
    toast(`已撤回 ${r.removed} 条未读`, 'ok'); await loadBroadcasts()
  } catch (e) { toast((e as Error).message, 'err') } finally { bcBusy.value = false }
}
async function remind(b: BroadcastItem) {
  bcBusy.value = true
  try {
    const r = await api.remindBroadcast(b.id)
    toast(r.unread > 0 ? `已向 ${r.unread} 位未读成员推送站外提醒` : '没有未读成员', r.unread > 0 ? 'ok' : 'info')
  } catch (e) { toast((e as Error).message, 'err') } finally { bcBusy.value = false }
}
async function saveDraft(scheduled: boolean) {
  bcBusy.value = true
  try {
    let at: number | null = null
    if (scheduled && draftTime.value) {
      const [h, m] = draftTime.value.split(':').map(Number)
      const d = new Date(); d.setHours(h, m, 0, 0)
      if (d.getTime() < Date.now()) d.setDate(d.getDate() + 1) // 已过时刻→明天
      at = d.getTime() / 1000
    }
    const ids = draftTarget.value === 'pick' && notifyTargets.value.length ? [...notifyTargets.value] : null
    await api.saveDraft(draftTitle.value.trim(), draftBody.value.trim(), ids, at)
    toast(at ? '定时已安排' : '草稿已保存', 'ok')
    draftTitle.value = ''; draftBody.value = ''; draftTime.value = ''
    await loadBroadcasts()
  } catch (e) { toast((e as Error).message, 'err') } finally { bcBusy.value = false }
}
async function sendDraft(b: BroadcastItem) {
  bcBusy.value = true
  try {
    const r = await api.sendDraft(b.id)
    toast(`已发送给 ${r.sent} 人`, 'ok'); await loadBroadcasts()
  } catch (e) { toast((e as Error).message, 'err') } finally { bcBusy.value = false }
}
async function deleteDraft(b: BroadcastItem) {
  if (!(await confirmDlg(`删除草稿「${b.title}」？`, { danger: true, title: '删除草稿' }))) return
  try {
    await api.deleteDraft(b.id); await loadBroadcasts()
  } catch (e) { toast((e as Error).message, 'err') }
}
function fmtTime(sec: number | null) {
  if (!sec) return '-'
  const d = new Date(sec * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(async () => {
  try { users.value = await api.adminUsers() } catch (e) { err.value = (e as Error).message }
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>通知运营</h1>
      <span style="font-size:12.5px;color:var(--text-3)">手动广播 · 发送记录 · 草稿定时</span>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 手动广播 -->
    <div class="card" style="padding:14px 16px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">📣 发送通知</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn sm" @click="notifyOpen = !notifyOpen">{{ notifyOpen ? '收起' : '撰写广播' }}</button>
      </div>
      <div v-if="notifyOpen" style="display:grid;gap:8px;background:var(--bg-2,#f6f6f8);border:1px solid var(--line);border-radius:10px;padding:10px">
        <input v-model="notifyTitle" maxlength="80" placeholder="标题（必填，如：明晚 20:00 全家网盘维护）" style="font-size:14px" />
        <textarea v-model="notifyBody" maxlength="500" rows="2" placeholder="正文（可留空）" style="font-size:14px;resize:vertical"></textarea>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <label style="display:flex;align-items:center;gap:4px;font-size:13px">
            <input type="checkbox" :checked="notifyTargets.length === 0" @change="notifyTargets = []" style="width:auto" /> 全体
          </label>
          <template v-for="u in users.filter(x => x.role !== 'admin' && x.is_active !== false)" :key="u.id">
            <label style="display:flex;align-items:center;gap:4px;font-size:13px">
              <input type="checkbox" :checked="notifyTargets.includes(u.id)"
                @change="toggleNotify(u.id)" style="width:auto" /> {{ u.nickname || u.username }}
            </label>
          </template>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;align-items:center">
          <span v-if="notifyOk" class="notify-ok">✓ 已发送</span>
          <button class="btn ghost sm" @click="notifyOpen = false">取消</button>
          <button class="btn sm notify-send" :disabled="notifyBusy || !notifyTitle.trim()" @click="sendNotify">
            {{ notifyBusy ? '推送中…' : '推送' }}</button>
        </div>
      </div>
      <p style="font-size:12px;color:var(--text-3);margin:0">
        站内通知逐人送达；已配置 Webhook 时同步推站外（Webhook 地址在
        <router-link to="/settings" style="color:var(--accent)">系统设置</router-link> 维护）。
      </p>
    </div>

    <!-- 通知管理：发送记录/撤回/提醒未读/草稿定时 -->
    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:10px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <div style="font-size:13.5px;font-weight:600">🗂️ 通知管理</div>
        <span style="font-size:12px;color:var(--text-3)">发送记录 · 撤回 · 草稿/定时</span>
        <div style="flex:1" />
        <button class="btn ghost sm" :disabled="bcBusy" @click="loadBroadcasts">{{ bcOpen ? '刷新' : '查看发送记录' }}</button>
      </div>
      <template v-if="bcOpen">
        <!-- 新建草稿 -->
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;background:var(--bg-2,#f6f6f8);border:1px solid var(--line);border-radius:10px;padding:10px">
          <input v-model="draftTitle" maxlength="80" placeholder="新草稿标题（必填）" style="flex:2;min-width:180px;font-size:14px" />
          <input v-model="draftBody" maxlength="500" placeholder="正文（可留空）" style="flex:3;min-width:200px;font-size:14px" />
          <select v-model="draftTarget" style="font-size:13px">
            <option value="all">全体</option>
            <option value="pick">指定成员…（推送面板勾选）</option>
          </select>
          <label style="display:flex;align-items:center;gap:4px;font-size:12.5px">
            定时 <input v-model="draftTime" type="time" style="width:96px;font-size:13px" />
          </label>
          <button class="btn sm" :disabled="bcBusy || !draftTitle.trim()" @click="saveDraft(true)">
            {{ draftTime ? '定时保存' : '存为草稿' }}</button>
        </div>
        <div v-if="!broadcasts.length" class="empty">还没有发送记录或草稿</div>
        <div v-for="b in broadcasts" :key="b.id" class="frow" style="cursor:default;align-items:flex-start">
          <span class="icon">{{ b.status === 'sent' ? '📣' : (b.status === 'scheduled' ? '⏰' : '📝') }}</span>
          <div class="name" style="flex:1">
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
              <b style="font-size:13.5px">{{ b.title }}</b>
              <span v-if="b.withdrawn_at" class="badge" style="background:var(--glass);color:var(--text-3)">已撤回</span>
              <span v-else-if="b.status === 'draft'" class="badge" style="background:var(--glass);color:var(--text-3)">草稿</span>
              <span v-else-if="b.status === 'scheduled'" class="badge" style="background:var(--glass);color:#b8860b">
                定时 {{ fmtTime(b.scheduled_at) }}</span>
              <span v-else-if="b.unread >= 0" class="badge" style="background:var(--glass);color:var(--text-3)">
                未读 {{ b.unread }} / 已读 {{ b.read }}</span>
            </div>
            <div style="font-size:12px;color:var(--text-3);white-space:pre-wrap">{{ b.body || '（无正文）' }}</div>
            <div style="font-size:11px;color:var(--text-3)">
              {{ b.status === 'sent' ? `发送给 ${b.target_count} 人 · ${fmtTime(b.sent_at)}` : `目标 ${b.target_count} 人` }} · {{ b.created_by }}
            </div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <template v-if="b.status === 'sent'">
              <button v-if="!b.withdrawn_at" class="btn ghost sm" :disabled="bcBusy" @click="withdraw(b)">撤回未读</button>
              <button class="btn ghost sm" :disabled="bcBusy" @click="remind(b)">提醒未读</button>
            </template>
            <template v-else>
              <button class="btn sm" :disabled="bcBusy" @click="sendDraft(b)">立即发送</button>
              <button class="btn ghost sm" :disabled="bcBusy" @click="deleteDraft(b)">删除</button>
            </template>
          </div>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.notify-send:disabled {
  opacity: 1;
  filter: saturate(.45) brightness(.94);
}
.notify-ok {
  font-size: 13px;
  color: #2e9e5b;
  font-weight: 600;
}
</style>
