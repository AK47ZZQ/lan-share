<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AdminUser, type ActivityItem } from '../api'
import { me } from '../auth'
import { fmtTs, toast } from '../ui'

const tab = ref<'users' | 'invites' | 'grants' | 'feed'>('users')
const users = ref<AdminUser[]>([])
const invites = ref<{ code: string; role: string; expired: boolean; used: boolean }[]>([])
const grants = ref<{ id: number; user_id: number; path: string; can_read: boolean; can_write: boolean }[]>([])
const feed = ref<ActivityItem[]>([])
const err = ref('')

// 授权表单
const gUser = ref<number | null>(null)
const gPath = ref('')
const gWrite = ref(false)

async function loadAll() {
  try {
    ;[users.value, invites.value, feed.value] = await Promise.all([
      api.adminUsers(), api.adminInvites(), api.activity(50),
    ])
    grants.value = await api.adminGrants()
  } catch (e) { err.value = (e as Error).message }
}

async function createInvite(role: string) {
  try {
    const r = await api.adminCreateInvite(role, 72)
    await navigator.clipboard?.writeText(`${location.origin}/login?invite=${r.code}`).catch(() => {})
    toast(`邀请码已复制：${r.code}（72 小时内有效，redeem 入口 /login）`, 'ok')
    await loadAll()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function toggleActive(u: AdminUser) {
  try { await api.adminPatchUser(u.id, { is_active: !u.is_active }); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
async function setQuota(u: AdminUser) {
  const v = prompt(`「${u.username}」私人空间配额（GB，留空取消限制）`,
    u.quota_bytes ? String(Math.round(u.quota_bytes / 2**30)) : '')
  if (v === null) return
  const gb = parseFloat(v)
  try { await api.adminPatchUser(u.id, { quota_bytes: gb > 0 ? Math.round(gb * 2**30) : null }); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
async function setRole(u: AdminUser) {
  const v = prompt(`「${u.username}」角色（admin / member / guest）`, u.role)
  if (!v) return
  try { await api.adminPatchUser(u.id, { role: v.trim() }); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
async function delUser(u: AdminUser) {
  if (!confirm(`删除成员「${u.username}」？其私人目录将移入 .orphaned/ 保留。`)) return
  try { await api.adminDeleteUser(u.id); await loadAll(); toast('已删除', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}
async function addGrant() {
  if (!gUser.value || !gPath.value.trim()) return
  try {
    await api.adminCreateGrant(gUser.value, gPath.value.trim(), true, gWrite.value)
    toast('授权已保存', 'ok'); gPath.value = ''; await loadAll()
  } catch (e) { toast((e as Error).message, 'err') }
}
function copyInvite(code: string) {
  navigator.clipboard?.writeText(code).then(() => toast('已复制', 'ok')).catch(() => toast(code, 'info'))
}
async function revokeInvite(code: string) {
  try { await api.adminDeleteInvite(code); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
async function delGrant(g: { id: number }) {
  try { await api.adminDeleteGrant(g.id); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}

onMounted(() => {
  if (me.value && me.value.role !== 'admin') {
    err.value = '需要管理员权限'
    return
  }
  void loadAll()
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>管理</h1>
      <nav class="tabs" style="flex:initial">
        <a href="#" :class="{ 'router-link-active': tab === 'users' }" @click.prevent="tab = 'users'">成员</a>
        <a href="#" :class="{ 'router-link-active': tab === 'invites' }" @click.prevent="tab = 'invites'">邀请</a>
        <a href="#" :class="{ 'router-link-active': tab === 'grants' }" @click.prevent="tab = 'grants'">授权</a>
        <a href="#" :class="{ 'router-link-active': tab === 'feed' }" @click.prevent="tab = 'feed'">动态</a>
      </nav>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 成员 -->
    <div v-if="tab === 'users'" class="card flist" style="padding:8px">
      <div v-for="u in users" :key="u.id" class="frow" style="cursor:default">
        <span class="icon" style="font-size:15px;font-weight:700">{{ u.username.slice(0, 1).toUpperCase() }}</span>
        <span class="name">
          <div>{{ u.username }} <span class="badge" :class="{ warn: u.role !== 'admin' }">{{ u.role }}</span>
            <span v-if="!u.is_active" class="badge danger">已停用</span></div>
          <small>加入于 {{ u.created_at }} · 配额 {{ u.quota_human }}</small>
        </span>
        <span class="ops" style="opacity:1">
          <button class="btn ghost sm" @click="setRole(u)">角色</button>
          <button class="btn ghost sm" @click="setQuota(u)">配额</button>
          <button class="btn ghost sm" @click="toggleActive(u)">{{ u.is_active ? '停用' : '启用' }}</button>
          <button class="iconbtn danger" title="删除成员" @click="delUser(u)">✕</button>
        </span>
      </div>
    </div>

    <!-- 邀请 -->
    <div v-if="tab === 'invites'">
      <div style="display:flex;gap:10px;margin-bottom:12px">
        <button class="btn sm" @click="createInvite('member')">＋ 邀请成员（可读写授权目录）</button>
        <button class="btn ghost sm" @click="createInvite('guest')">＋ 邀请教访（仅授权目录只读）</button>
      </div>
      <div class="card flist" style="padding:8px">
        <div v-if="!invites.length" class="empty">还没有邀请记录</div>
        <div v-for="i in invites" :key="i.code" class="frow" style="cursor:default">
          <span class="icon">🎟️</span>
          <span class="name"><div style="font-family:monospace">{{ i.code }}</div>
            <small>{{ i.role }} · {{ i.used ? '已使用' : i.expired ? '已过期' : '有效' }}</small></span>
          <span class="ops" style="opacity:1">
            <button class="btn ghost sm" @click="copyInvite(i.code)">复制</button>
            <button class="iconbtn danger" v-if="!i.used && !i.expired" @click="revokeInvite(i.code)">✕</button>
          </span>
        </div>
      </div>
      <p style="font-size:12.5px;color:var(--text-3)">成员在登录页使用邀请码自助建号（登录页会显示 redeem 入口）。</p>
    </div>

    <!-- 授权 -->
    <div v-if="tab === 'grants'">
      <div class="card" style="padding:16px;display:grid;gap:10px;margin-bottom:12px">
        <div class="grid2">
          <label class="field">成员
            <select v-model.number="gUser">
              <option :value="null" disabled>选择成员…</option>
              <option v-for="u in users.filter(x => x.role !== 'admin')" :key="u.id" :value="u.id">
                {{ u.username }}（{{ u.role }}）
              </option>
            </select>
          </label>
          <label class="field">共享目录路径（如 docs 或 照片/2024）
            <input v-model="gPath" placeholder="shared 空间下的目录" />
          </label>
        </div>
        <label style="display:flex;gap:8px;align-items:center;font-size:14px">
          <input type="checkbox" v-model="gWrite" style="width:auto" /> 授予写入（默认只读）
        </label>
        <div><button class="btn sm" @click="addGrant">保存授权</button></div>
      </div>
      <div class="card flist" style="padding:8px">
        <div v-if="!grants.length" class="empty">暂无授权</div>
        <div v-for="g in grants" :key="g.id" class="frow" style="cursor:default">
          <span class="icon">🔑</span>
          <span class="name">
            <div>{{ g.path }} <span class="badge" :class="{ warn: !g.can_write }">{{ g.can_write ? '读写' : '只读' }}</span></div>
            <small>成员 #{{ g.user_id }}：{{ users.find(u => u.id === g.user_id)?.username || '（已删除）' }}</small>
          </span>
          <span class="ops" style="opacity:1">
            <button class="iconbtn danger" @click="delGrant(g)">✕</button>
          </span>
        </div>
      </div>
    </div>

    <!-- 动态 -->
    <div v-if="tab === 'feed'" class="card flist" style="padding:8px">
      <div v-if="!feed.length" class="empty">暂无动态</div>
      <div v-for="(a, i) in feed" :key="i" class="frow" style="cursor:default">
        <span class="icon">{{ a.action.startsWith('share') ? '🔗' : a.action === 'upload' || a.action === 'upload_instant' ? '⬆️' : a.action === 'invite_redeemed' ? '👋' : '✨' }}</span>
        <span class="name">
          <div>{{ a.username || '（成员）' }} {{ { upload: '上传了', upload_instant: '秒传了', mkdir: '新建了目录', share: '创建了分享', share_download: '分享被下载', invite_redeemed: '加入了家庭空间', trash: '删除了', restore: '恢复了', move: '移动了', rename: '重命名了' }[a.action] || a.action }}
            <b>{{ a.target.split('/').pop() }}</b></div>
          <small>{{ a.target }}</small>
        </span>
        <span class="meta">{{ fmtTs(a.ts) }}</span>
      </div>
    </div>
  </main>
</template>
