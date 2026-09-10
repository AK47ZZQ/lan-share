<script setup lang="ts">
// 治理工具（v3.39 新增）：大文件 TOP / 疑似重复（同尺寸聚类）/ 会话与设备吊销。
// 数据源：/api/admin/large-files · /api/admin/duplicates · /api/admin/sessions|devices。
import { computed, onMounted, ref } from 'vue'
import { api, type AdminDevice, type AdminSession, type AdminUser, type DupGroup, type LargeFileItem } from '../api'
import { confirmDlg, fmtTs, human, toast } from '../ui'

const err = ref('')
const tab = ref<'large' | 'dupes' | 'access'>('large')

// 大文件
const lfItems = ref<LargeFileItem[]>([])
const lfTotal = ref(0)
const lfSize = ref(0)
const lfBusy = ref(false)
async function loadLarge() {
  lfBusy.value = true
  try {
    const r = await api.largeFiles(50)
    lfItems.value = r.items; lfTotal.value = r.total_files; lfSize.value = r.total_size
  } catch (e) { toast((e as Error).message, 'err') } finally { lfBusy.value = false }
}

// 疑似重复
const dupeGroups = ref<DupGroup[]>([])
const dupeCount = ref(0)
const dupeMinMb = ref(1)
const dupeBusy = ref(false)
async function loadDupes() {
  dupeBusy.value = true
  try {
    const r = await api.duplicates(Math.max(0, dupeMinMb.value) * 1024 * 1024, 50)
    dupeGroups.value = r.groups; dupeCount.value = r.group_count
  } catch (e) { toast((e as Error).message, 'err') } finally { dupeBusy.value = false }
}

// 会话与设备（v3.40 增强：按账号筛选 / 按账号汇总与一键下线 / 全站清空）
const sessions = ref<AdminSession[]>([])
const devices = ref<AdminDevice[]>([])
const accessBusy = ref(false)
const accessFilter = ref('')
const usersMap = ref<Record<string, number>>({})
const filteredSessions = computed(() => {
  const q = accessFilter.value.trim().toLowerCase()
  return q ? sessions.value.filter(x => x.username.toLowerCase().includes(q)) : sessions.value
})
const filteredDevices = computed(() => {
  const q = accessFilter.value.trim().toLowerCase()
  return q ? devices.value.filter(x => x.username.toLowerCase().includes(q)) : devices.value
})
const accountSummary = computed(() => {
  const m = new Map<string, { sessions: number; devices: number }>()
  for (const x of sessions.value)
    m.set(x.username, { sessions: (m.get(x.username)?.sessions || 0) + 1, devices: m.get(x.username)?.devices || 0 })
  for (const x of devices.value)
    m.set(x.username, { sessions: m.get(x.username)?.sessions || 0, devices: (m.get(x.username)?.devices || 0) + 1 })
  return [...m.entries()].sort((a, b) => (b[1].sessions + b[1].devices) - (a[1].sessions + a[1].devices))
})
async function loadAccess() {
  accessBusy.value = true
  try {
    const [sx, dx, ux] = await Promise.all([api.adminSessions(), api.adminDevices(), api.adminUsers()])
    sessions.value = sx.items; devices.value = dx.items
    usersMap.value = Object.fromEntries(ux.map(x => [x.username, x.id]))
  } catch (e) { toast((e as Error).message, 'err') } finally { accessBusy.value = false }
}
async function kickAccount(username: string, scope: 'all' | 'web' | 'app') {
  const uid = usersMap.value[username]
  if (!uid) { toast('找不到该账号', 'err'); return }
  const label = scope === 'all' ? '「' + username + '」的全部 Web 会话与 App 设备'
    : scope === 'web' ? '「' + username + '」的全部 Web 会话'
    : '「' + username + '」的全部 App 设备'
  if (!(await confirmDlg('一键下线' + label + '？对方需重新登录/配对。', { danger: true, okText: '一键下线' }))) return
  try {
    const r = await api.kickUser(uid, scope)
    toast('已下线：Web 会话 ' + r.sessions + ' 个、App 设备 ' + r.devices + ' 台', 'ok')
    await loadAccess()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function purgeAllSessions() {
  if (!(await confirmDlg('清空全站所有 Web 会话（保留你当前这个）？所有成员将被登出。', { danger: true, okText: '全部下线' }))) return
  try {
    const r = await api.purgeSessions()
    toast('已吊销 ' + r.revoked + ' 个会话', 'ok'); await loadAccess()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function killSession(s: AdminSession) {
  if (!(await confirmDlg(`吊销「${s.username}」的这个 Web 会话？对方下一步操作即被登出。`, { danger: true, okText: '吊销' }))) return
  try { await api.revokeAdminSession(s.token_hash); toast('已吊销', 'ok'); await loadAccess() } catch (e) { toast((e as Error).message, 'err') }
}
async function killDevice(d: AdminDevice) {
  if (!(await confirmDlg(`吊销「${d.username}」的设备「${d.name}」？该 App 需重新配对登录。`, { danger: true, okText: '吊销' }))) return
  try { await api.revokeAdminDevice(d.id); toast('已吊销', 'ok'); await loadAccess() } catch (e) { toast((e as Error).message, 'err') }
}

onMounted(() => { void loadLarge() })
function switchTab(t: 'large' | 'dupes' | 'access') {
  tab.value = t
  if (t === 'large' && !lfItems.value.length && !lfBusy.value) void loadLarge()
  if (t === 'dupes' && !dupeCount.value && !dupeBusy.value) void loadDupes()
  if (t === 'access' && !sessions.value.length && !accessBusy.value) void loadAccess()
}
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>治理工具</h1>
      <nav class="tabs" style="flex:1">
        <a href="#" :class="{ 'router-link-active': tab === 'large' }" @click.prevent="switchTab('large')">大文件</a>
        <a href="#" :class="{ 'router-link-active': tab === 'dupes' }" @click.prevent="switchTab('dupes')">疑似重复</a>
        <a href="#" :class="{ 'router-link-active': tab === 'access' }" @click.prevent="switchTab('access')">会话与设备</a>
      </nav>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 大文件 TOP -->
    <div v-if="tab === 'large'">
      <p style="font-size:12.5px;color:var(--text-3);margin:0 0 10px">
        全库按体积排序 TOP 50 · 共 {{ lfTotal }} 个常规文件 / {{ human(lfSize) }}（隐藏目录不计）。
        <button class="btn ghost sm" :disabled="lfBusy" @click="loadLarge">{{ lfBusy ? '统计中…' : '重新扫描' }}</button>
      </p>
      <div class="card flist" style="padding:8px">
        <div v-if="!lfItems.length" class="empty">{{ lfBusy ? '扫描中…' : '暂无文件' }}</div>
        <div v-for="f in lfItems" :key="f.path" class="frow" style="cursor:default">
          <span class="icon">📦</span>
          <span class="name">
            <div style="font-size:13.5px;word-break:break-all">{{ f.path }}</div>
            <small>修改于 {{ fmtTs(f.mtime) }}</small>
          </span>
          <span class="ops"><span class="badge">{{ human(f.size) }}</span></span>
        </div>
      </div>
    </div>

    <!-- 疑似重复 -->
    <div v-if="tab === 'dupes'">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
        <span style="font-size:12.5px;color:var(--text-3)">
          同尺寸聚类（不做全盘哈希，相同体积即归组，请人工核对）· 共 {{ dupeCount }} 组疑似</span>
        <label style="display:flex;align-items:center;gap:5px;font-size:13px">
          ≥ <input v-model.number="dupeMinMb" type="number" min="0" style="width:76px;font-size:13px" /> MB
        </label>
        <button class="btn ghost sm" :disabled="dupeBusy" @click="loadDupes">{{ dupeBusy ? '扫描中…' : '扫描' }}</button>
      </div>
      <div class="card" style="padding:10px;display:grid;gap:10px">
        <div v-if="!dupeGroups.length" class="empty">{{ dupeBusy ? '扫描中…' : '没有发现疑似重复' }}</div>
        <div v-for="(g, i) in dupeGroups" :key="i" style="border:1px solid var(--line);border-radius:10px;padding:10px">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
            <span class="badge">{{ human(g.size) }} / 个</span>
            <span class="badge">{{ g.paths.length }} 个文件</span>
            <span class="badge warn">约浪费 {{ human(g.wasted) }}</span>
          </div>
          <div v-for="p in g.paths" :key="p" style="font-size:12.5px;word-break:break-all;color:var(--text-2)">📄 {{ p }}</div>
        </div>
      </div>
    </div>

    <!-- 会话与设备 -->
    <div v-if="tab === 'access'" style="display:grid;gap:12px">
      <div class="card" style="padding:10px 12px;display:grid;gap:8px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <div style="font-size:13.5px;font-weight:600">👤 按账号下线</div>
          <input v-model="accessFilter" placeholder="按账号筛选…" style="flex:1;min-width:160px;font-size:13.5px;max-width:220px" />
          <button class="btn danger sm" style="margin-left:auto" @click="purgeAllSessions">⚠ 全部下线（保留我）</button>
          <button class="btn ghost sm" :disabled="accessBusy" @click="loadAccess">刷新</button>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <span v-for="[name, c] in accountSummary" :key="name"
                style="display:inline-flex;gap:6px;align-items:center;padding:4px 6px 4px 10px;border:1px solid var(--line);border-radius:999px;font-size:12.5px">
            <b>{{ name }}</b>
            <span style="color:var(--text-3)">会话 {{ c.sessions }} · 设备 {{ c.devices }}</span>
            <button class="btn ghost sm" style="padding:2px 8px" title="该账号全部下线" @click="kickAccount(name, 'all')">一键下线</button>
          </span>
          <span v-if="!accountSummary.length" style="font-size:12.5px;color:var(--text-3)">没有活跃账号</span>
        </div>
      </div>

      <div class="card" style="padding:10px;display:grid;gap:2px">
        <div style="display:flex;gap:8px;align-items:center;padding:4px 6px">
          <div style="font-size:13.5px;font-weight:600">💻 Web 会话（活跃 {{ filteredSessions.length }}<span v-if="accessFilter"> / {{ sessions.length }}</span>）</div>
          <div style="flex:1" />
        </div>
        <div v-if="!filteredSessions.length" class="empty">没有匹配的会话</div>
        <div v-for="s in filteredSessions" :key="s.token_hash" class="frow" style="cursor:default">
          <span class="icon">💻</span>
          <span class="name">
            <div style="font-size:13.5px">{{ s.username }} <span style="color:var(--text-3);font-size:12px">{{ s.ip }}{{ s.ua ? ' · ' + s.ua.slice(0, 60) : '' }}</span></div>
            <small>登录于 {{ fmtTs(s.created_at) }} · 过期 {{ fmtTs(s.expires_at) }}</small>
          </span>
          <span class="ops" style="opacity:1">
            <button class="btn ghost sm" @click="killSession(s)">吊销</button>
          </span>
        </div>
      </div>

      <div class="card" style="padding:10px;display:grid;gap:2px">
        <div style="display:flex;gap:8px;align-items:center;padding:4px 6px">
          <div style="font-size:13.5px;font-weight:600">📱 App 设备（活跃 {{ filteredDevices.length }}<span v-if="accessFilter"> / {{ devices.length }}</span>）</div>
          <div style="flex:1" />
        </div>
        <div v-if="!filteredDevices.length" class="empty">没有匹配的设备</div>
        <div v-for="d in filteredDevices" :key="d.id" class="frow" style="cursor:default">
          <span class="icon">📱</span>
          <span class="name">
            <div style="font-size:13.5px">{{ d.name }} <span style="color:var(--text-3);font-size:12px">· {{ d.username }}</span></div>
            <small>配对于 {{ fmtTs(d.created_at) }} · 最近活跃 {{ d.last_seen ? fmtTs(d.last_seen) : '从未' }}</small>
          </span>
          <span class="ops" style="opacity:1">
            <button class="btn ghost sm" @click="killDevice(d)">吊销</button>
          </span>
        </div>
      </div>

      <div class="card" style="padding:12px 14px">
        <div style="font-size:13.5px;font-weight:600;margin-bottom:4px">📜 最近审计</div>
        <p style="font-size:12px;color:var(--text-3);margin:0">
          登录/分享/删除等操作留痕在 <router-link to="/status" style="color:var(--accent)">运行状态</router-link> 的审计日志区可查。
        </p>
      </div>
    </div>
  </main>
</template>
