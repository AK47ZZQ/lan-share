// 全局认证状态（App 壳与 Login/Up/Admin 视图共享）——
// 登录/登出必须经此模块更新，否则壳层守卫会把用户弹回登录页
import { ref } from 'vue'
import { api, type Me } from './api'

export const me = ref<Me | null>(null)
export const authReady = ref(false)

export async function refreshAuth(): Promise<Me | null> {
  // 裸 fetch：/api/auth/me 的 401 是"未登录"的正常探测结果，
  // 不能走 api.me()（会触发全局 401 跳登录）
  try {
    const r = await fetch('/api/auth/me')
    me.value = r.ok ? await r.json() : null
  } catch {
    me.value = null
  }
  authReady.value = true
  return me.value
}

export async function login(username: string, password: string): Promise<Me> {
  await api.login(username, password)
  return refreshAuth() as Promise<Me>
}

export async function logout(): Promise<void> {
  await api.logout()
  me.value = null
}
