// 全局认证状态（App 壳与 Login/Up/Admin 视图共享）——
// 登录/登出必须经此模块更新，否则壳层守卫会把用户弹回登录页
import { ref } from 'vue'
import { api, type Me } from './api'

export const me = ref<Me | null>(null)
export const authReady = ref(false)

export async function refreshAuth(): Promise<Me | null> {
  try {
    me.value = await api.me()
  } catch {
    me.value = null
  }
  authReady.value = true
  return me.value
}

export async function login(username: string, password: string): Promise<Me> {
  await api.login(username, password)
  me.value = await api.me()
  authReady.value = true
  return me.value
}

export async function logout(): Promise<void> {
  await api.logout()
  me.value = null
}
