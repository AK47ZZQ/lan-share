<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type AdminUser, type Grant } from '../api'
import { me } from '../auth'
import { setPreviewAs } from '../previewAs'
import Modal from '../components/Modal.vue'
import AdminUserModal from '../components/AdminUserModal.vue'
import { confirmDlg, toast } from '../ui'

const router = useRouter()
function viewAs(username: string) {
  setPreviewAs(username)
  router.push('/files')  // 内容区（v3.39：/ 已改为管理台总览）
  toast(`已切换到「${username}」视角浏览（只读预览）`, 'ok')
}

const tab = ref<'users' | 'invites' | 'grants'>('users')
const users = ref<AdminUser[]>([])
const invites = ref<{ code: string; role: string; expired: boolean; used: boolean }[]>([])
const grants = ref<Grant[]>([])
const err = ref('')

// 授权表单（批量）
const gUsers = ref<number[]>([])
const gPaths = ref<string[]>([])
const gWrite = ref(false)
const gExpiry = ref(0)            // 0=永久
const dirOptions = ref<{ path: string; label: string }[]>([])
const grantFilter = ref<number | 'all'>('all')

const memberOptions = computed(() => users.value.filter(u => u.role !== 'admin'))
function memberName(id: number) {
  const u = users.value.find(u => u.id === id)
  return (u?.nickname || '').trim() || u?.username || `#${id}`
}
function userDisplay(u: AdminUser) { return (u.nickname || '').trim() || u.username }

async function loadDirs() {
  // v0.20 授权重构：只取共享空间一级目录（子目录随父级授权自动继承，
  // 服务端 admin_create_grant 也已只收一级路径）
  const out: { path: string; label: string }[] = []
  try {
    const r = await api.list('')
    for (const e of r.items.filter(x => x.is_dir && !x.is_private_root && x.path !== 'private' && x.path !== 'incoming')) {
      if (e.name === 'private' || e.name === 'incoming') continue
      out.push({ path: e.path, label: e.path })
    }
  } catch { /* 无权目录跳过 */ }
  dirOptions.value = out
}

async function addGrantBatch() {
  if (!gUsers.value.length || !gPaths.value.length) { toast('先选成员和目录', 'err'); return }
  let ok = 0, fail = 0
  for (const uid of gUsers.value) {
    for (const p of gPaths.value) {
      try { await api.adminCreateGrant(uid, p, true, gWrite.value, gExpiry.value || undefined); ok++ }
      catch (e) { fail++; toast(`${memberName(uid)} × ${p}: ${(e as Error).message}`, 'err') }
    }
  }
  if (ok) toast(`已创建 ${ok} 条授权（${gUsers.value.length} 位成员 × ${gPaths.value.length} 个目录）`, 'ok')
  await loadAll()
}

const grantsFiltered = computed(() =>
  grantFilter.value === 'all' ? grants.value : grants.value.filter(g => g.user_id === grantFilter.value))
const memberGrantCount = (uid: number) => grants.value.filter(g => g.user_id === uid).length

async function loadAll() {
  try {
    ;[users.value, invites.value] = await Promise.all([
      api.adminUsers(), api.adminInvites(),
    ])
    grants.value = await api.adminGrants()
    void loadDirs()
  } catch (e) { err.value = (e as Error).message }
}

async function createInvite(role: string) {
  try {
    const r = await api.adminCreateInvite(role, 72)
    let copied = true
    try { await navigator.clipboard?.writeText(`${location.origin}/login?invite=${r.code}`) } catch { copied = false }
    // 剪贴板失败不再谎报"已复制"（R1-P3-15）：码在 toast 文本里仍可手动复制
    toast(copied ? `邀请码已复制：${r.code}（72 小时内有效）`
                 : `已生成邀请码：${r.code}（复制失败，请手动复制，72 小时内有效）`, copied ? 'ok' : 'info')
    await loadAll()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function toggleActive(u: AdminUser) {
  try { await api.adminPatchUser(u.id, { is_active: !u.is_active }); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
const quotaModal = ref<{ user: AdminUser; value: string } | null>(null)
const credModal = ref<AdminUser | null>(null)
// <10GB 一位小数：避免 512MB 显示成 "0"（0 现在是"清除"语义，会被误触发）
function gbText(b: number): string {
  const gb = b / 2 ** 30
  return gb < 10 ? gb.toFixed(1) : String(Math.round(gb))
}
function openQuota(u: AdminUser) {
  quotaModal.value = { user: u, value: u.quota_bytes == null ? '' : gbText(u.quota_bytes) }
}
// 数据导出逃生门：打包成员私人空间+直传子树（含 manifest）
async function exportUser(u: AdminUser) {
  if (!(await confirmDlg(
    `导出「${u.username}」的全部数据（私人空间 + 直传子树，zip 下载一次即失效）？`,
    { title: '导出成员数据' }))) return
  try {
    const r = await api.exportMember(u.username)
    const a = document.createElement('a')
    a.href = r.url
    a.download = r.name
    document.body.appendChild(a)
    a.click()
    a.remove()
    toast(`已导出 ${r.files} 个文件，开始下载`, 'ok')
  } catch (e) { toast((e as Error).message, 'err') }
}
async function saveQuota() {
  const m = quotaModal.value
  if (!m) return
  const t = m.value.trim()
  if (t) {
    const gb = parseFloat(t)
    if (!isFinite(gb) || gb < 0) { toast('请输入非负数字，或留空提交表示不限', 'err'); return }
  }
  const gb = parseFloat(t)
  // 契约（与 App 0.8.2 同修）：服务端 UserPatchBody 的 null = "未提供"会被跳过，
  // 清除配额必须显式发 0（服务端 0 → NULL = 不限）
  const q = t && gb > 0 ? Math.round(gb * 2 ** 30) : 0
  try {
    await api.adminPatchUser(m.user.id, { quota_bytes: q })
    toast(`「${m.user.username}」配额已更新`, 'ok')
    quotaModal.value = null
    await loadAll()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function changeRole(u: AdminUser, ev: Event) {
  const role = (ev.target as HTMLSelectElement).value
  try { await api.adminPatchUser(u.id, { role }); toast('角色已更新', 'ok'); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}

async function delUser(u: AdminUser) {
  if (!(await confirmDlg(`删除成员「${u.username}」？其私人目录将移入 .orphaned/ 保留。`, { danger: true, okText: '删除' }))) return
  try { await api.adminDeleteUser(u.id); await loadAll(); toast('已删除', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}
function copyInvite(code: string) {
  navigator.clipboard?.writeText(code).then(() => toast('已复制', 'ok')).catch(() => toast(code, 'info'))
}
async function revokeInvite(code: string) {
  // 二次确认（R1-P2-6）：误点 ✕ 立即失效且无法撤销撤销
  if (!(await confirmDlg(`撤销邀请码 ${code.slice(0, 6)}…？撤销后立即失效`, { danger: true, okText: '撤销' }))) return
  try { await api.adminDeleteInvite(code); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
async function delGrant(g: { id: number }) {
  if (!(await confirmDlg('撤销这条目录授权？成员将立即失去对应访问权', { danger: true, okText: '撤销' }))) return
  try { await api.adminDeleteGrant(g.id); await loadAll() } catch (e) { toast((e as Error).message, 'err') }
}
onMounted(async () => {
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
      <h1>成员与权限</h1>
      <nav class="tabs" style="flex:1">
        <a href="#" :class="{ 'router-link-active': tab === 'users' }" @click.prevent="tab = 'users'">成员</a>
        <a href="#" :class="{ 'router-link-active': tab === 'invites' }" @click.prevent="tab = 'invites'">邀请</a>
        <a href="#" :class="{ 'router-link-active': tab === 'grants' }" @click.prevent="tab = 'grants'">授权</a>
      </nav>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 成员 -->
    <div v-if="tab === 'users'" class="card flist" style="padding:8px">
      <div v-for="u in users" :key="u.id" class="frow" style="cursor:default">
        <span class="icon" style="font-size:15px;font-weight:700">{{ u.username.slice(0, 1).toUpperCase() }}</span>
        <span class="name">
          <div>{{ userDisplay(u) }} <span v-if="(u.nickname || '').trim()" style="font-size:11.5px;color:var(--text-3)">@{{ u.username }}</span> <span class="badge" :class="{ warn: u.role !== 'admin' }">{{ u.role }}</span>
            <span v-if="u.totp_enabled" class="badge" title="已启用两步验证">🛡️ 2FA</span>
            <span v-if="!u.is_active" class="badge danger">已停用</span></div>
          <small>加入于 {{ u.created_at }} · 配额 {{ u.quota_human }}</small>
        </span>
        <span class="ops" style="opacity:1">
          <select :value="u.role" style="padding:6px 8px;font-size:13px" @change="changeRole(u, $event)">
            <option value="admin">admin</option><option value="member">member</option><option value="guest">guest</option>
          </select>
          <button v-if="u.username !== me?.username" class="btn ghost sm" title="以此成员的视角浏览（只读预览）" @click="viewAs(u.username)">👁 视角</button>
          <button class="btn ghost sm" title="修改用户名/重置密码/两步验证/WebDAV 密码" @click="credModal = u">✏️ 账号</button>
          <button class="btn ghost sm" title="设置私人空间配额" @click="openQuota(u)">📏 配额</button>
          <button class="btn ghost sm" title="导出该成员数据（私人空间+直传子树 zip）" @click="exportUser(u)">📦 导出</button>
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
            <button class="iconbtn danger" title="删除记录" @click="revokeInvite(i.code)">✕</button>
          </span>
        </div>
      </div>
      <p style="font-size:12.5px;color:var(--text-3)">成员在登录页使用邀请码自助建号（登录页会显示 redeem 入口）。</p>
    </div>

    <!-- 授权 -->
    <div v-if="tab === 'grants'">
      <div class="card" style="padding:14px 16px;display:grid;gap:10px;margin-bottom:12px">
        <div style="font-size:13.5px;font-weight:600">➕ 新建授权（可批量）</div>
        <div class="grid2">
          <label class="field">成员（可多选）
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
              <label v-for="u in memberOptions" :key="u.id"
                     style="display:inline-flex;align-items:center;gap:4px;font-size:13px;padding:4px 9px;border:1px solid var(--line);border-radius:999px;cursor:pointer"
                     :style="gUsers.includes(u.id) ? 'border-color:var(--accent);background:var(--hover)' : ''">
                <input type="checkbox" style="width:auto;display:none" :value="u.id"
                       v-model="gUsers" />{{ u.username }}
              </label>
              <span v-if="!memberOptions.length" style="color:var(--text-3);font-size:12.5px">暂无成员</span>
            </div>
          </label>
          <label class="field">共享目录（一级目录，子目录自动继承）
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;max-height:132px;overflow:auto">
              <label v-for="d in dirOptions" :key="d.path"
                     style="display:inline-flex;align-items:center;gap:4px;font-size:13px;padding:4px 9px;border:1px solid var(--line);border-radius:999px;cursor:pointer"
                     :style="gPaths.includes(d.path) ? 'border-color:var(--accent);background:var(--hover)' : ''">
                <input type="checkbox" style="width:auto;display:none" :value="d.path" v-model="gPaths" />📁 {{ d.label }}
              </label>
              <span v-if="!dirOptions.length" style="color:var(--text-3);font-size:12.5px">共享空间还没有目录</span>
            </div>
          </label>
        </div>
        <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
          <label style="display:flex;gap:6px;align-items:center;font-size:14px">
            <input type="checkbox" v-model="gWrite" style="width:auto" /> 可写入（默认只读）
          </label>
          <label class="field" style="flex-direction:row;align-items:center;gap:6px">有效期
            <select v-model.number="gExpiry" style="padding:6px 8px;font-size:13px;width:auto">
              <option :value="0">永久</option>
              <option :value="24">1 天</option>
              <option :value="168">7 天</option>
              <option :value="720">30 天</option>
            </select>
          </label>
          <button class="btn sm" style="margin-left:auto" @click="addGrantBatch">保存授权（{{ gUsers.length || 0 }} 人 × {{ gPaths.length || 0 }} 目录）</button>
        </div>
      </div>

      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
        <span style="font-size:13px;color:var(--text-2)">查看：</span>
        <button class="btn ghost sm" :class="{ '': grantFilter === 'all' }" @click="grantFilter = 'all'"
                :style="grantFilter === 'all' ? 'border-color:var(--accent);color:var(--accent)' : ''">全部成员</button>
        <button v-for="u in memberOptions" :key="u.id" class="btn ghost sm"
                :style="grantFilter === u.id ? 'border-color:var(--accent);color:var(--accent)' : ''"
                @click="grantFilter = u.id">{{ u.username }}（{{ memberGrantCount(u.id) }}）</button>
      </div>

      <div class="card flist" style="padding:8px">
        <div v-if="!grantsFiltered.length" class="empty">暂无授权</div>
        <div v-for="g in grantsFiltered" :key="g.id" class="frow" style="cursor:default">
          <span class="icon">🔑</span>
          <span class="name">
            <div>{{ g.path }} <span class="badge" :class="{ warn: !g.can_write }">{{ g.can_write ? '读写' : '只读' }}</span></div>
            <small>{{ memberName(g.user_id) }}<template v-if="g.expires_at"> · {{ g.expires_at <= Date.now() / 1000 ? '已过期' : '至 ' + new Date(g.expires_at * 1000).toLocaleDateString() }}</template><template v-else> · 永久</template></small>
          </span>
          <span class="ops" style="opacity:1">
            <button class="iconbtn danger" title="撤销授权" @click="delGrant(g)">✕</button>
          </span>
        </div>
      </div>
    </div>

    <!-- 成员账号弹窗（改用户名/重置密码） -->
    <AdminUserModal v-if="credModal" :user="credModal" @close="credModal = null" @changed="loadAll" />

    <!-- 配额弹窗（全页签可用） -->
    <Modal v-if="quotaModal" :title="`设置配额 — ${quotaModal.user.username}`" @close="quotaModal = null">
      <label class="field">私人空间配额（GB，清空则不限制）
        <input v-model="quotaModal.value" type="number" min="0" step="1" placeholder="不限"
               @keydown.enter="saveQuota" />
      </label>
      <p style="font-size:12.5px;color:var(--text-3)">按逻辑字节计：秒传命中也计入；共享空间不受配额限制。</p>
      <div class="actions">
        <button class="btn ghost sm" @click="quotaModal = null">取消</button>
        <button class="btn sm" @click="saveQuota">保存</button>
      </div>
    </Modal>
  </main>
</template>


