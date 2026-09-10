<script setup lang="ts">
// 治理工具（v3.39 新增）：大文件 TOP / 疑似重复（同尺寸聚类）/ 会话与设备吊销。
// 数据源：/api/admin/large-files · /api/admin/duplicates · /api/admin/sessions|devices。
import { onMounted, ref } from 'vue'
import { api, type AdminDevice, type AdminSession, type DupGroup, type LargeFileItem } from '../api'
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

// 会话与设备
const sessions = ref<AdminSession[]>([])
const devices = ref<AdminDevice[]>([])
const accessBusy = ref(false)
async function loadAccess() {
  accessBusy.value = true
  try {
    const [s, d] = await Promise.all([api.adminSessions(), api.adminDevices()])
    sessions.value = s.items; devices.value = d.items
  } catch (e) { toast((e as Error).message, 'err') } finally { accessBusy.value = false }
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
      <div class="card" style="padding:10px;display:grid;gap:2px">
        <div style="display:flex;gap:8px;align-items:center;padding:4px 6px">
          <div style="font-size:13.5px;font-weight:600">💻 Web 会话（活跃 {{ sessions.length }}）</div>
          <div style="flex:1" />
          <button class="btn ghost sm" :disabled="accessBusy" @click="loadAccess">刷新</button>
        </div>
        <div v-if="!sessions.length" class="empty">没有活跃会话</div>
        <div v-for="s in sessions" :key="s.token_hash" class="frow" style="cursor:default">
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
          <div style="font-size:13.5px;font-weight:600">📱 App 设备（活跃 {{ devices.length }}）</div>
          <div style="flex:1" />
        </div>
        <div v-if="!devices.length" class="empty">没有活跃设备</div>
        <div v-for="d in devices" :key="d.id" class="frow" style="cursor:default">
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
