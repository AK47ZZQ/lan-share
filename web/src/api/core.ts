// API 客户端公共件（v3.48 从 api.ts 按域拆分）：响应处理与请求助手。
// 各域模块（auth/console/notify/files/photos/shares）都从这里取助手；
// 401 跳登录的公开页豁免与成员视角预览头在这里统一实现。
import { previewAs } from '../previewAs'

export async function handle<T>(r: Response): Promise<T> {
  if (r.status === 401) {
    // 会话过期：整页跳登录（带 next 回跳）。公开页（/s/ 分享、/up 直传、/login）
    // 与登录态探测 /api/auth/me 不触发——否则未登录访客会被误弹登录页
    // （/up 与 App.vue 的 publicRoute 保持一致，9.9 复审 P2）
    const onPublicPage = location.pathname === '/login' || location.pathname === '/up'
      || location.pathname.startsWith('/s/')
    if (!onPublicPage) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search)
    }
    throw Object.assign(new Error('未登录'), { unauthorized: true })
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try { const b = await r.json(); detail = typeof b.detail === 'string' ? b.detail : detail } catch { /* */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}
// enc 已迁往 ../pure（vue-free 可测），re-export 保持 `from './core'` 的调用方不变
export { enc } from '../pure'
export const jpost = (url: string, body: unknown) =>
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
// 成员视角预览：只读 GET 附带 X-As-User（后端仅 admin 生效，其余请求一律原身份）
export const getAs = (url: string) =>
  fetch(url, previewAs.value ? { headers: { 'X-As-User': previewAs.value } } : undefined)
