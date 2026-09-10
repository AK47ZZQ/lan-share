<script setup lang="ts">
// 邀请页签（v3.49 从 AdminView 按 tab 拆出）：建码/复制/撤销；数据由父传入。
import { api } from '../../api'
import { confirmDlg, toast } from '../../ui'

const props = defineProps<{ invites: { code: string; role: string; expired: boolean; used: boolean }[] }>()
const emit = defineEmits<{ refresh: [] }>()

async function createInvite(role: string) {
  try {
    const r = await api.adminCreateInvite(role, 72)
    let copied = true
    try { await navigator.clipboard?.writeText(`${location.origin}/login?invite=${r.code}`) } catch { copied = false }
    // 剪贴板失败不再谎报"已复制"（R1-P3-15）：码在 toast 文本里仍可手动复制
    toast(copied ? `邀请码已复制：${r.code}（72 小时内有效）`
                 : `已生成邀请码：${r.code}（复制失败，请手动复制，72 小时内有效）`, copied ? 'ok' : 'info')
    emit('refresh')
  } catch (e) { toast((e as Error).message, 'err') }
}
function copyInvite(code: string) {
  navigator.clipboard?.writeText(code).then(() => toast('已复制', 'ok')).catch(() => toast(code, 'info'))
}
async function revokeInvite(code: string) {
  // 二次确认（R1-P2-6）：误点 ✕ 立即失效且无法撤销撤销
  if (!(await confirmDlg(`撤销邀请码 ${code.slice(0, 6)}…？撤销后立即失效`, { danger: true, okText: '撤销' }))) return
  try { await api.adminDeleteInvite(code); emit('refresh') } catch (e) { toast((e as Error).message, 'err') }
}
</script>

<template>
  <div>
    <div style="display:flex;gap:10px;margin-bottom:12px">
      <button class="btn sm" @click="createInvite('member')">＋ 邀请成员（可读写授权目录）</button>
      <button class="btn ghost sm" @click="createInvite('guest')">＋ 邀请教访（仅授权目录只读）</button>
    </div>
    <div class="card flist" style="padding:8px">
      <div v-if="!props.invites.length" class="empty">还没有邀请记录</div>
      <div v-for="i in props.invites" :key="i.code" class="frow" style="cursor:default">
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
</template>
