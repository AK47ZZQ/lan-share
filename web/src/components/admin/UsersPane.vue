<script setup lang="ts">
// 成员页签（v3.49 从 AdminView 按 tab 拆出）：列表/角色/视角预览/账号/
// 配额/导出/踢下线/停用/删除；数据由父组件传入，变更后 emit('refresh')。
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type AdminUser } from '../../api'
import { me } from '../../auth'
import { setPreviewAs } from '../../previewAs'
import Modal from '../Modal.vue'
import AdminUserModal from '../AdminUserModal.vue'
import { confirmDlg, toast } from '../../ui'

const props = defineProps<{ users: AdminUser[] }>()
const emit = defineEmits<{ refresh: [] }>()

const router = useRouter()
function viewAs(username: string) {
  setPreviewAs(username)
  router.push('/files')  // 内容区（v3.39：/ 已改为管理台总览）
  toast(`已切换到「${username}」视角浏览（只读预览）`, 'ok')
}

function userDisplay(u: AdminUser) { return (u.nickname || '').trim() || u.username }

async function toggleActive(u: AdminUser) {
  try { await api.adminPatchUser(u.id, { is_active: !u.is_active }); emit('refresh') } catch (e) { toast((e as Error).message, 'err') }
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
    emit('refresh')
  } catch (e) { toast((e as Error).message, 'err') }
}
async function changeRole(u: AdminUser, ev: Event) {
  const role = (ev.target as HTMLSelectElement).value
  try { await api.adminPatchUser(u.id, { role }); toast('角色已更新', 'ok'); emit('refresh') } catch (e) { toast((e as Error).message, 'err') }
}

// 一键下线（v3.40 紧急操作）：Web 会话 + App 设备一起清，对方需重新登录/配对
async function kickMember(u: AdminUser) {
  if (!(await confirmDlg(`一键下线「${u.username}」的全部 Web 会话与 App 设备？对方需重新登录/配对。`, { danger: true, okText: '踢下线' }))) return
  try {
    const r = await api.kickUser(u.id, 'all')
    toast(`已下线：Web 会话 ${r.sessions} 个、App 设备 ${r.devices} 台`, 'ok')
  } catch (e) { toast((e as Error).message, 'err') }
}
async function delUser(u: AdminUser) {
  if (!(await confirmDlg(`删除成员「${u.username}」？其私人目录将移入 .orphaned/ 保留。`, { danger: true, okText: '删除' }))) return
  try { await api.adminDeleteUser(u.id); emit('refresh'); toast('已删除', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}
</script>

<template>
  <div class="card flist" style="padding:8px">
    <div v-for="u in props.users" :key="u.id" class="frow" style="cursor:default">
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
        <button v-if="u.username !== me?.username" class="btn ghost sm" title="一键下线该成员的全部 Web 会话与 App 设备（紧急操作）" @click="kickMember(u)">🚪 踢下线</button>
        <button class="btn ghost sm" @click="toggleActive(u)">{{ u.is_active ? '停用' : '启用' }}</button>
        <button class="iconbtn danger" title="删除成员" @click="delUser(u)">✕</button>
      </span>
    </div>
  </div>

  <!-- 成员账号弹窗（改用户名/重置密码） -->
  <AdminUserModal v-if="credModal" :user="credModal" @close="credModal = null" @changed="emit('refresh')" />

  <!-- 配额弹窗 -->
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
</template>
