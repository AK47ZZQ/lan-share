// 反馈域（v4.0.9 P1）：提交 / 列表 / 详情 / 追加说明。
// 走 multipart —— 附件直接随表单上传，服务端边读边写、超 25MB 即断。
import { handle, jpost } from './core'

export type FeedbackType = 'idea' | 'bug' | 'question' | 'other'

/** 状态机：已提交 → 已查看 → 处理中 → 已完成 / 不处理 */
export type FeedbackStatus = '已提交' | '已查看' | '处理中' | '已完成' | '不处理'

export interface FeedbackItem {
  id: string
  type: FeedbackType
  title: string
  status: FeedbackStatus
  note: string          // 开发端回写的一句话
  app: string           // 版本
  device: string
  user: string
  created: string
  updated: string
  images: string[]
  body?: string
  /** 上报状态（P2 起填充）：本地 | 已上报 | 未上报 | 上报失败 */
  relay?: string
  /** 公开到 GitHub 后的链接（P4） */
  issue_url?: string
}

export interface FeedbackCreateInput {
  title: string
  type: FeedbackType
  body: string
  app_ver: string
  device: string
  files: File[]
  /** P4：勾选后中继端代发 GitHub Issue（用户无需账号） */
  public_to_github?: boolean
}

export const feedbackApi = {
  list: () => fetch('/api/feedback').then(r => handle<{ items: FeedbackItem[] }>(r))
    .then(d => d.items || []),

  create: (input: FeedbackCreateInput) => {
    const fd = new FormData()
    fd.append('title', input.title)
    fd.append('type', input.type)
    fd.append('body', input.body)
    fd.append('app_ver', input.app_ver)
    fd.append('device', input.device)
    if (input.public_to_github) fd.append('public_to_github', '1')
    for (const f of input.files) fd.append('files', f)
    return fetch('/api/feedback', { method: 'POST', body: fd })
      .then(r => handle<{ id: string; relay?: Record<string, string> }>(r))
  },

  detail: (fid: string) =>
    fetch(`/api/feedback/${encodeURIComponent(fid)}`)
      .then(r => handle<FeedbackItem>(r)),

  append: (fid: string, body: string) =>
    jpost(`/api/feedback/${encodeURIComponent(fid)}/append`, { body })
      .then(r => handle<{ ok: boolean }>(r)),
}

/** 状态 → 颜色（列表和详情共用，避免两处色值漂移） */
export function statusClass(s: string): string {
  switch (s) {
    case '已完成': return 'st-done'
    case '不处理': return 'st-skip'
    case '处理中': return 'st-doing'
    case '已查看': return 'st-seen'
    default: return 'st-new'
  }
}

export const TYPE_LABEL: Record<string, string> = {
  idea: '想法', bug: '问题', question: '疑问', other: '其他',
}
