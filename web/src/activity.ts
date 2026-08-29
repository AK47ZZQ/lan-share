// 动态流共享工具：图标/文案/相对时间/用户名标注/点击跳转（通知面板与 /activity 页共用）
import type { ActivityItem, Me } from './api'

export const ACTION_ICON: Record<string, string> = {
  upload: '📤', upload_instant: '⚡', mkdir: '📁', share: '🔗', share_target: '🎯',
  share_download: '📥', invite_redeemed: '👋', trash: '🗑️', restore: '♻️',
  move: '📦', rename: '✏️',
}

export const ACTION_TEXT: Record<string, string> = {
  upload: '上传了', upload_instant: '秒传了', mkdir: '新建了目录', share: '创建了分享',
  share_target: '定向分享了', share_download: '的分享被下载', invite_redeemed: '加入了家庭空间',
  trash: '删除了', restore: '恢复了', move: '移动了', rename: '重命名了',
}

/** 操作者标注：审计早期行无用户名——链接下载显示"链接访客"，其余为"早期记录" */
export function who(n: ActivityItem): string {
  if (n.username) return n.username
  return n.action === 'share_download' ? '链接访客' : '早期记录'
}

export function baseName(target: string): string {
  return target.split('/').filter(Boolean).pop() || target
}

export function activityTitle(n: ActivityItem): string {
  const verb = ACTION_TEXT[n.action] || n.action
  if (n.count > 1) return `${who(n)} ${verb} ${n.count} 项`
  if (n.action === 'share_target' && n.detail) return `${who(n)} 定向分享了 ${baseName(n.target)} ${n.detail}`
  return `${who(n)} ${verb} ${baseName(n.target)}`
}

export function relTime(ts: number): string {
  const d = Date.now() / 1000 - ts
  if (d < 60) return '刚刚'
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`
  if (d < 7 * 86400) return `${Math.floor(d / 86400)} 天前`
  const dt = new Date(ts * 1000)
  return `${dt.getMonth() + 1}-${dt.getDate()} ${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`
}

/** 直达跳转：动态 → 对应目录/页面；null = 不可直达 */
export function activityRoute(n: ActivityItem): { path: string; query?: Record<string, string> } | null {
  const target = n.target || ''
  const dirOf = (p: string) => p.split('/').filter(Boolean).slice(0, -1).join('/')
  const toDir = (d: string) => d ? { path: '/', query: { dir: d } } : { path: '/' }
  switch (n.action) {
    case 'upload': case 'upload_instant':
      return toDir(dirOf(target))
    case 'mkdir':
      return toDir(target)
    case 'move': case 'rename':
      return toDir(dirOf(target))
    case 'trash': case 'restore':
      return { path: '/trash' }
    case 'share': case 'share_target': case 'share_download':
      return { path: '/shares' }
    default:
      return null
  }
}

/** invite_redeemed 仅 admin 可跳管理面板 */
export function activityRouteFor(me: Me, n: ActivityItem) {
  if (n.action === 'invite_redeemed') return me.role === 'admin' ? { path: '/admin' } : null
  return activityRoute(n)
}
