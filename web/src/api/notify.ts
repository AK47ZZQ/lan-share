// 通知与动态域（v3.48 从 api.ts 按域拆分）：全员站内通知/家庭动态/成员名单。
import { handle, jpost } from './core'
import type { ActivityItem, Member, NotificationItem } from './types'

export const notifyApi = {
  notifications: () => fetch('/api/notifications').then(r => handle<{ unread: number; items: NotificationItem[] }>(r)),
  notificationsRead: () => jpost('/api/notifications/read', {}).then(r => handle<{ ok: boolean }>(r)),
  // 全员端：单条已读/删除
  notificationRead: (id: number) => jpost(`/api/notifications/${id}/read`, {}).then(r => handle<{ ok: boolean }>(r)),
  notificationDelete: (id: number) =>
    fetch(`/api/notifications/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  // 家庭动态流（全员）
  activity: (limit = 40) => fetch(`/api/activity?limit=${limit}`).then(r => handle<ActivityItem[]>(r)),
  members: () => fetch('/api/members').then(r => handle<Member[]>(r)),
}
