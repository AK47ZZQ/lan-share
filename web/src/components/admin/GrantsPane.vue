<script setup lang="ts">
// 授权页签（v3.49 从 AdminView 按 tab 拆出）：批量建授权/按成员筛选/撤销；
// users（成员选项）与 grants 数据由父传入。
import { computed, ref } from 'vue'
import { api, type AdminUser, type Grant } from '../../api'
import { confirmDlg, toast } from '../../ui'

const props = defineProps<{ users: AdminUser[]; grants: Grant[]; dirOptions: { path: string; label: string }[] }>()
const emit = defineEmits<{ refresh: [] }>()

// 授权表单（批量）
const gUsers = ref<number[]>([])
const gPaths = ref<string[]>([])
const gWrite = ref(false)
const gExpiry = ref(0)            // 0=永久
const grantFilter = ref<number | 'all'>('all')

const memberOptions = computed(() => props.users.filter(u => u.role !== 'admin'))
function memberName(id: number) {
  const u = props.users.find(u => u.id === id)
  return (u?.nickname || '').trim() || u?.username || `#${id}`
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
  emit('refresh')
}

const grantsFiltered = computed(() =>
  grantFilter.value === 'all' ? props.grants : props.grants.filter(g => g.user_id === grantFilter.value))
const memberGrantCount = (uid: number) => props.grants.filter(g => g.user_id === uid).length

async function delGrant(g: { id: number }) {
  if (!(await confirmDlg('撤销这条目录授权？成员将立即失去对应访问权', { danger: true, okText: '撤销' }))) return
  try { await api.adminDeleteGrant(g.id); emit('refresh') } catch (e) { toast((e as Error).message, 'err') }
}
</script>

<template>
  <div>
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
            <label v-for="d in props.dirOptions" :key="d.path"
                   style="display:inline-flex;align-items:center;gap:4px;font-size:13px;padding:4px 9px;border:1px solid var(--line);border-radius:999px;cursor:pointer"
                   :style="gPaths.includes(d.path) ? 'border-color:var(--accent);background:var(--hover)' : ''">
              <input type="checkbox" style="width:auto;display:none" :value="d.path" v-model="gPaths" />📁 {{ d.label }}
            </label>
            <span v-if="!props.dirOptions.length" style="color:var(--text-3);font-size:12.5px">共享空间还没有目录</span>
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
</template>
