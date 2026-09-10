// 认证/资料/账号/设备域（v3.48 从 api.ts 按域拆分）。
import { handle, jpost } from './core'
import type { DeviceInfo, Me } from './types'

export const authApi = {
  me: () => fetch('/api/auth/me').then(r => handle<Me>(r)),
  login: (username: string, password: string) => jpost('/api/login', { username, password }).then(r => handle<{ ok?: boolean; totp_required?: boolean; username?: string }>(r)),
  loginTotp: (username: string, password: string, code: string) => jpost('/api/login/totp', { username, password, code }).then(r => handle<{ ok: boolean }>(r)),
  totpSetup: () => jpost('/api/auth/totp/setup', {}).then(r => handle<{ secret: string; otpauth: string }>(r)),
  totpEnable: (code: string) => jpost('/api/auth/totp/enable', { code }).then(r => handle<{ ok: boolean; recovery_codes: string[] }>(r)),
  totpDisable: (old_password: string) => jpost('/api/auth/totp/disable', { old_password }).then(r => handle<{ ok: boolean }>(r)),
  totpRcRegen: (old_password: string) => jpost('/api/auth/totp/rc', { old_password }).then(r => handle<{ ok: boolean; recovery_codes: string[] }>(r)),
  logout: () => fetch('/api/logout', { method: 'POST' }).then(() => undefined),
  changeUsername: (new_username: string, password: string) =>
    jpost('/api/auth/username', { new_username, password }).then(r => handle<{ ok: boolean; username: string }>(r)),
  changePassword: (old_password: string, new_password: string) =>
    jpost('/api/auth/password', { old_password, new_password }).then(r => handle<{ ok: boolean }>(r)),
  // 资料（v0.17）：昵称只是显示层，登录仍用用户名；头像按 uid 存服务端
  updateProfile: (nickname: string) =>
    fetch('/api/auth/profile', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nickname }) }).then(r => handle<{ ok: boolean; nickname: string }>(r)),
  uploadAvatar: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return fetch('/api/auth/avatar', { method: 'POST', body: fd }).then(r => handle<{ ok: boolean; avatar_path: string }>(r))
  },
  avatarUrl: (username: string, bust: number | string = 0) => `/api/auth/avatar/${encodeURIComponent(username)}?t=${bust}`,
  webdavPwdStatus: () => fetch('/api/auth/webdav-password').then(r => handle<{ set: boolean }>(r)),
  setWebdavPwd: (current_password: string, webdav_password: string) =>
    jpost('/api/auth/webdav-password', { current_password, webdav_password }).then(r => handle<{ ok: boolean; set: boolean }>(r)),

  // 手机 App 免密配对：生成一次性 6 位码（3 分钟有效）
  pairCode: () => jpost('/api/pair/code', {}).then(r => handle<{ code: string; expires_in: number }>(r)),

  // 登录设备管理（本账号）：App 配对出的设备在此可见/可吊销
  listDevices: () => fetch('/api/auth/devices').then(r => handle<{ devices: DeviceInfo[] }>(r)),
  revokeDevice: (id: number) => fetch(`/api/auth/devices/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
}
