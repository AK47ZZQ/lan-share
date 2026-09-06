<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type Me } from '../api'
import { avatarBust, refreshAuth } from '../auth'
import { toast, promptDlg, confirmDlg } from '../ui'

const props = defineProps<{ me: Me }>()
const emit = defineEmits<{ close: []; updated: [] }>()

// 改用户名
const newName = ref('')
const namePwd = ref('')
const nameBusy = ref(false)
const nameErr = ref('')

// 改密码
const oldPwd = ref('')
const newPwd = ref('')
const pwdBusy = ref(false)
const pwdErr = ref('')

// 两步验证（TOTP）
const totpEnabled = ref(!!props.me.totp_enabled)
const totpSecret = ref('')
const totpUri = ref('')
const totpCode = ref('')
const totpBusy = ref(false)
const totpErr = ref('')
const totpDisablePwd = ref('')
// 资料（v0.17）：头像 + 昵称。昵称只是显示层，登录仍用用户名
const nickName = ref((props.me.nickname || '').trim())
const nickBusy = ref(false)
const avatarBusy = ref(false)
const avatarInput = ref<HTMLInputElement | null>(null)
const initial = computed(() => (props.me.username || '?').slice(0, 1).toUpperCase())

function pickAvatar() { avatarInput.value?.click() }

async function onAvatarChange(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f) return
  if (!f.type.startsWith('image/')) { toast('请选择图片文件', 'err'); return }
  avatarBusy.value = true
  try {
    await api.uploadAvatar(f)
    avatarBust.value++
    toast('头像已更新', 'ok')
    await refreshAuth()
    emit('updated')
  } catch (e) { toast((e as Error).message, 'err') } finally { avatarBusy.value = false }
}

async function saveNickname() {
  nickBusy.value = true
  try {
    const r = await api.updateProfile(nickName.value.trim())
    toast(r.nickname ? `昵称已保存：${r.nickname}` : '昵称已清空，恢复显示用户名', 'ok')
    await refreshAuth()
    emit('updated')
  } catch (e) { toast((e as Error).message, 'err') } finally { nickBusy.value = false }
}

// 恢复码：明文仅开通/重新生成的响应里出现一次，展示到用户点"已妥善保存"为止
const rcCodes = ref<string[]>([])
const rcLeft = ref(props.me.totp_rc_left ?? 0)
const rcBusy = ref(false)

async function totpSetup() {
  totpErr.value = ''
  totpBusy.value = true
  try {
    const r = await api.totpSetup()
    totpSecret.value = r.secret
    totpUri.value = r.otpauth
  } catch (e) { totpErr.value = (e as Error).message } finally { totpBusy.value = false }
}

async function totpEnable() {
  totpErr.value = ''
  totpBusy.value = true
  try {
    const r = await api.totpEnable(totpCode.value)
    totpEnabled.value = true
    rcCodes.value = r.recovery_codes
    rcLeft.value = r.recovery_codes.length
    totpSecret.value = ''; totpUri.value = ''; totpCode.value = ''
    toast('两步验证已启用，请保存恢复码', 'ok')
    await refreshAuth()
    emit('updated')
  } catch (e) { totpErr.value = (e as Error).message } finally { totpBusy.value = false }
}

async function regenRc() {
  try {
    const pwd = await promptDlg('输入当前密码确认（旧恢复码全部作废）：', '', { title: '重新生成恢复码', placeholder: '当前密码' })
    if (pwd === null) return
    rcBusy.value = true
    const r = await api.totpRcRegen(pwd)
    rcCodes.value = r.recovery_codes
    rcLeft.value = r.recovery_codes.length
    toast('恢复码已重新生成，旧码全部作废', 'ok')
  } catch (e) { toast((e as Error).message, 'err') } finally { rcBusy.value = false }
}

async function copyRc() {
  try { await navigator.clipboard.writeText(rcCodes.value.map(c => c.slice(0, 4) + '-' + c.slice(4)).join('\n')); toast('恢复码已复制，请存入密码管理器或抄录', 'ok') }
  catch { toast('复制失败，请手动抄录', 'err') }
}

function rcDone() { rcCodes.value = [] }

async function totpDisable() {
  totpErr.value = ''
  totpBusy.value = true
  try {
    await api.totpDisable(totpDisablePwd.value)
    totpEnabled.value = false
    totpDisablePwd.value = ''
    toast('两步验证已关闭', 'ok')
    await refreshAuth()
    emit('updated')
  } catch (e) { totpErr.value = (e as Error).message } finally { totpBusy.value = false }
}

async function copySecret() {
  try { await navigator.clipboard.writeText(totpSecret.value); toast('密钥已复制', 'ok') }
  catch { toast('复制失败，请手动记录密钥', 'err') }
}

// 登录设备管理（App 配对出的设备 Token 在此可见/可吊销）
import type { DeviceInfo } from '../api'
const devices = ref<DeviceInfo[]>([])
const devicesBusy = ref(false)
const devicesErr = ref('')

function relTime(ts: number): string {
  if (!ts) return '从未'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 90) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}

async function loadDevices() {
  devicesErr.value = ''
  try { devices.value = (await api.listDevices()).devices }
  catch (e) { devicesErr.value = (e as Error).message }
}

async function revokeDevice(d: DeviceInfo) {
  const tip = d.current
    ? '这是当前正在使用的浏览器，吊销后本页登录会立即失效，需要重新登录。确定吊销？'
    : `吊销设备「${d.name}」后，该设备将无法再访问（下次请求即被拒）。确定吊销？`
  if (!(await confirmDlg(tip, { danger: true, title: '吊销设备' }))) return
  devicesBusy.value = true
  try {
    await api.revokeDevice(d.id)
    toast(d.current ? '已吊销当前设备，请重新登录' : `设备「${d.name}」已吊销`, 'ok')
    await loadDevices()
  } catch (e) { toast((e as Error).message, 'err') } finally { devicesBusy.value = false }
}

// 手机 App 连接（扫码填地址 + 配对码免密登录）
const pairCode = ref('')
const pairLeft = ref(0)          // 剩余秒数
const pairBusy = ref(false)
const pairErr = ref('')
let pairTimer: ReturnType<typeof setInterval> | null = null

const serverUrl = window.location.origin

function stopPairTimer() {
  if (pairTimer) { clearInterval(pairTimer); pairTimer = null }
}

async function genPairCode() {
  pairErr.value = ''
  pairBusy.value = true
  stopPairTimer()
  try {
    const r = await api.pairCode()
    pairCode.value = r.code
    pairLeft.value = r.expires_in
    pairTimer = setInterval(() => {
      pairLeft.value--
      if (pairLeft.value <= 0) { pairLeft.value = 0; stopPairTimer() }
    }, 1000)
  } catch (e) { pairErr.value = (e as Error).message } finally { pairBusy.value = false }
}

function copyPairUrl() {
  try { navigator.clipboard.writeText(serverUrl); toast('地址已复制', 'ok') }
  catch { toast('复制失败', 'err') }
}

onBeforeUnmount(stopPairTimer)

// WebDAV 独立密码
const davSet = ref(false)
const davEditing = ref(false)
const davCur = ref('')
const davNew = ref('')
const davBusy = ref(false)
const davErr = ref('')
onMounted(async () => {
  try { davSet.value = (await api.webdavPwdStatus()).set } catch { /* 忽略，默认未设置 */ }
  loadDevices()
})

async function saveDav() {
  davErr.value = ''
  davBusy.value = true
  try {
    const r = await api.setWebdavPwd(davCur.value, davNew.value)
    davSet.value = r.set
    davEditing.value = false; davCur.value = ''; davNew.value = ''
    toast(r.set ? 'WebDAV 独立密码已生效（WebDAV 客户端需改用新密码）' : '已清除：WebDAV 回退用登录密码挂载', 'ok')
  } catch (e) { davErr.value = (e as Error).message } finally { davBusy.value = false }
}

async function doRename() {
  nameErr.value = ''
  nameBusy.value = true
  try {
    const r = await api.changeUsername(newName.value.trim(), namePwd.value)
    toast(`用户名已改为「${r.username}」`, 'ok')
    await refreshAuth()
    emit('updated')
    emit('close')
  } catch (e) { nameErr.value = (e as Error).message } finally { nameBusy.value = false }
}

async function doPwd() {
  pwdErr.value = ''
  pwdBusy.value = true
  try {
    await api.changePassword(oldPwd.value, newPwd.value)
    toast('密码已修改（其他设备已全部登出）', 'ok')
    await refreshAuth()
    emit('close')
  } catch (e) { pwdErr.value = (e as Error).message } finally { pwdBusy.value = false }
}
</script>

<template>
  <teleport to="body">
    <div class="overlay" @click.self="emit('close')">
      <div class="modal-card">
        <h3>账号设置</h3>
        <p style="font-size:13px;color:var(--text-3);margin:0 0 14px">
          当前账号：<b>{{ props.me.username }}</b>（{{ props.me.role === 'admin' ? '管理员' : props.me.role === 'guest' ? '访客' : '成员' }}）
        </p>

        <!-- 资料卡（v0.17）：头像 + 昵称 -->
        <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:16px">
          <img v-if="props.me.avatar_path" class="profile-avatar"
               :src="api.avatarUrl(props.me.username, avatarBust)" alt="头像" />
          <span v-else class="profile-avatar profile-avatar-fallback">{{ initial }}</span>
          <div style="display:grid;gap:7px;flex:1;min-width:230px">
            <div style="font-size:13.5px;font-weight:600">🧑 资料</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <input v-model="nickName" placeholder="昵称（留空恢复显示用户名）" maxlength="32"
                     style="flex:1;min-width:150px" @keydown.enter="saveNickname" />
              <button class="btn sm" :disabled="nickBusy" @click="saveNickname">
                {{ nickBusy ? '保存中…' : '保存昵称' }}
              </button>
              <button class="btn ghost sm" :disabled="avatarBusy" @click="pickAvatar">
                {{ avatarBusy ? '上传中…' : '更换头像' }}
              </button>
            </div>
            <p style="font-size:11.5px;color:var(--text-3);margin:0">
              登录仍使用用户名；设置昵称后界面各处优先显示昵称。头像支持常见图片格式，8MB 内。
            </p>
          </div>
          <input ref="avatarInput" type="file" accept="image/*" style="display:none" @change="onAvatarChange" />
        </div>

        <form @submit.prevent="doRename" style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">✏️ 修改用户名</div>
          <input v-model="newName" placeholder="新用户名" autocomplete="username" />
          <input v-model="namePwd" type="password" placeholder="当前密码（确认）" autocomplete="current-password" />
          <button class="btn sm" :disabled="nameBusy || !newName || !namePwd">
            {{ nameBusy ? '提交中…' : '修改用户名' }}
          </button>
          <p v-if="nameErr" class="err" style="margin:0">{{ nameErr }}</p>
        </form>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <form @submit.prevent="doPwd" style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🔑 修改密码</div>
          <input v-model="oldPwd" type="password" placeholder="当前密码" autocomplete="current-password" />
          <input v-model="newPwd" type="password" placeholder="新密码（至少 8 位）" autocomplete="new-password" />
          <button class="btn sm" :disabled="pwdBusy || !oldPwd || !newPwd || newPwd.length < 8">
            {{ pwdBusy ? '提交中…' : '修改密码' }}
          </button>
          <p style="font-size:12px;color:var(--text-3);margin:0">修改后其他设备的登录会全部失效，当前设备保持登录。</p>
          <p v-if="pwdErr" class="err" style="margin:0">{{ pwdErr }}</p>
        </form>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <!-- 两步验证 -->
        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🛡️ 两步验证（TOTP）</div>
          <template v-if="totpEnabled">
            <p style="font-size:12.5px;color:var(--text-3);margin:0">已启用。登录时需要输入认证器（如 Google Authenticator、Microsoft Authenticator）的 6 位动态码。</p>
            <template v-if="rcCodes.length">
              <div style="display:grid;gap:8px;background:var(--glass);border:1px solid var(--line);border-radius:10px;padding:12px">
                <div style="font-size:13px;font-weight:600">🔑 一次性恢复码（仅显示这一次）</div>
                <p style="font-size:12px;color:var(--text-3);margin:0">手机丢失、认证器卸载时，在登录第二步输入任意一枚恢复码即可登录，用一枚少一枚。请复制存入密码管理器或抄录在纸上。</p>
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px">
                  <code v-for="c in rcCodes" :key="c" style="user-select:all;background:var(--glass);padding:4px 8px;border-radius:6px;border:1px solid var(--line);font-size:12.5px;text-align:center">{{ c.slice(0, 4) }}-{{ c.slice(4) }}</code>
                </div>
                <div style="display:flex;gap:8px">
                  <button class="btn sm" @click="copyRc">📋 复制全部</button>
                  <button class="btn ghost sm" @click="rcDone">已妥善保存</button>
                </div>
              </div>
            </template>
            <p style="font-size:12.5px;color:var(--text-2);margin:0">
              一次性恢复码剩余 <b>{{ rcLeft }}</b> 枚（认证器不可用时顶替动态码，用一枚少一枚）。{{ rcLeft === 0 ? '恢复码已用完且无法再看密钥，建议立即重新生成。' : '' }}
            </p>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn ghost sm" :disabled="rcBusy" @click="regenRc">🔄 重新生成恢复码</button>
            </div>
            <input v-model="totpDisablePwd" type="password" placeholder="当前密码（确认关闭）" autocomplete="current-password" />
            <button class="btn danger sm" :disabled="totpBusy || !totpDisablePwd" @click="totpDisable">
              {{ totpBusy ? '提交中…' : '关闭两步验证' }}
            </button>
          </template>
          <template v-else-if="totpUri">
            <p style="font-size:12.5px;color:var(--text-2);margin:0">1️⃣ 用认证器 App（Microsoft/Google 验证器）扫二维码，或直接用 <b>LAN Share 手机 App「我的 → 验证器」</b>扫码绑定，也可手动输入密钥：</p>
            <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
              <img :src="`/api/qr?text=${encodeURIComponent(totpUri)}`"
                   style="width:150px;height:150px;background:#fff;padding:6px;border-radius:10px" alt="二维码" />
              <div style="display:grid;gap:6px;font-size:12.5px">
                <code style="user-select:all;background:var(--glass);padding:6px 8px;border-radius:8px;border:1px solid var(--line)">{{ totpSecret }}</code>
                <button class="btn ghost sm" @click="copySecret">📋 复制密钥</button>
              </div>
            </div>
            <p style="font-size:12.5px;color:var(--text-2);margin:0">2️⃣ 输入认证器显示的 6 位动态码完成开通：</p>
            <input v-model="totpCode" placeholder="6 位动态验证码" inputmode="numeric" maxlength="6" style="width:180px" @keydown.enter="totpEnable" />
            <div style="display:flex;gap:8px">
              <button class="btn sm" :disabled="totpBusy || totpCode.length !== 6" @click="totpEnable">确认开通</button>
              <button class="btn ghost sm" @click="totpUri = ''; totpSecret = ''; totpCode = ''">取消</button>
            </div>
          </template>
          <template v-else>
            <p style="font-size:12.5px;color:var(--text-3);margin:0">为账号增加一层保护：登录除密码外还需手机认证器的动态码。管理员重置密码会同步解除。没有认证器 App？装了 LAN Share App 的手机可直接用「我的 → 验证器」代替。</p>
            <button class="btn sm" :disabled="totpBusy" @click="totpSetup">启用两步验证</button>
          </template>
          <p v-if="totpErr" class="err" style="margin:0">{{ totpErr }}</p>
        </div>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <!-- 手机 App 连接 -->
        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">📱 手机 App 连接</div>
          <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
            <img :src="`/api/qr?text=${encodeURIComponent(serverUrl)}`"
                 style="width:120px;height:120px;background:#fff;padding:5px;border-radius:10px" alt="服务器地址二维码" />
            <div style="display:grid;gap:6px;font-size:12.5px;max-width:300px">
              <span style="color:var(--text-2)">手机 App 登录页点「扫码」图标，扫这里免手输地址：</span>
              <code style="user-select:all;background:var(--glass);padding:5px 8px;border-radius:8px;border:1px solid var(--line);font-size:11.5px">{{ serverUrl }}</code>
              <button class="btn ghost sm" @click="copyPairUrl" style="justify-self:start">📋 复制地址</button>
            </div>
          </div>
          <div style="font-size:12.5px;color:var(--text-2)">
            长辈的手机不想输密码？在 App 登录页选「配对码登录」，输入这里生成的 6 位码即可（3 分钟内有效，一码一用）。
            配对出的设备可随时在「设备管理」吊销。
          </div>
          <template v-if="pairCode">
            <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
              <span :style="`font-size:34px;font-weight:700;letter-spacing:8px;font-variant-numeric:tabular-nums;${pairLeft > 0 ? '' : 'opacity:.4'}`">{{ pairCode }}</span>
              <span style="font-size:12px;color:var(--text-3)">{{ pairLeft > 0 ? `${pairLeft}s 后过期` : '已过期，请重新生成' }}</span>
            </div>
          </template>
          <button class="btn sm" :disabled="pairBusy" @click="genPairCode" style="justify-self:start">
            {{ pairBusy ? '生成中…' : pairCode ? '重新生成' : '生成配对码' }}
          </button>
          <p v-if="pairErr" class="err" style="margin:0">{{ pairErr }}</p>
        </div>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <!-- 登录设备管理 -->
        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">📲 登录设备</div>
          <p style="font-size:12.5px;color:var(--text-3);margin:0">
            手机 App 配对后留在本账号的长期凭据。手机丢失或淘汰时，在这里吊销对应设备即可（其他设备不受影响）。
          </p>
          <template v-if="devices.length">
            <div v-for="d in devices" :key="d.id"
                 style="display:flex;align-items:center;gap:10px;background:var(--glass);border:1px solid var(--line);border-radius:10px;padding:9px 12px">
              <div style="flex:1;min-width:0;display:grid;gap:2px">
                <span style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                  {{ d.name }}<span v-if="d.current" style="margin-left:6px;font-size:11px;color:var(--accent,#4a9eff)">● 本机</span>
                </span>
                <span style="font-size:11.5px;color:var(--text-3)">最近活跃 {{ relTime(d.last_seen) }}</span>
              </div>
              <button class="btn danger sm" :disabled="devicesBusy" @click="revokeDevice(d)">吊销</button>
            </div>
          </template>
          <p v-else style="font-size:12.5px;color:var(--text-3);margin:0">还没有 App 设备——用上面的二维码或配对码连接后会出现。</p>
          <p v-if="devicesErr" class="err" style="margin:0">{{ devicesErr }}</p>
        </div>

        <div style="height:1px;background:var(--line);margin:16px 0" />

        <!-- WebDAV 独立密码 -->
        <div style="display:grid;gap:9px">
          <div style="font-size:13.5px;font-weight:600">🗄️ WebDAV 独立密码</div>
          <p style="font-size:12.5px;color:var(--text-3);margin:0">
            {{ davSet
              ? '已启用：WebDAV 挂载只认独立密码，登录密码对 /dav/ 无效。两者互相独立，可单独轮换。'
              : '未设置：WebDAV 挂载（RaiDrive / Windows 映射 / 手机 ES 管理器等）目前使用登录密码。' }}
          </p>
          <template v-if="davEditing">
            <input v-model="davCur" type="password" placeholder="当前登录密码（确认）" autocomplete="current-password" />
            <input v-model="davNew" type="password" placeholder="WebDAV 新密码（至少 8 位，留空=清除）" autocomplete="new-password" />
            <div style="display:flex;gap:8px">
              <button class="btn sm" :disabled="davBusy || !davCur" @click="saveDav">
                {{ davBusy ? '提交中…' : (davNew ? '保存' : '清除独立密码') }}
              </button>
              <button class="btn ghost sm" @click="davEditing = false; davErr = ''">取消</button>
            </div>
          </template>
          <template v-else>
            <button class="btn sm" @click="davEditing = true">{{ davSet ? '修改 / 清除' : '设置独立密码' }}</button>
          </template>
          <p v-if="davErr" class="err" style="margin:0">{{ davErr }}</p>
        </div>

        <div class="actions"><button class="btn ghost sm" @click="emit('close')">关闭</button></div>
      </div>
    </div>
  </teleport>
</template>
