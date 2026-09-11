// 纯函数集（vue-free）：node --test 可直接 import，是 web/test 的被测对象。
//
// 为什么单独拆一个文件：ui.ts 依赖 vue 的响应式（reactive），而 node 无法加载
// ESM bundler 构建的 vue——纯函数留在 ui.ts 里等于"测试不可达"（web 测试批次
// D2 的结论：能被钉住的先决条件是"能在测试环境里加载"）。
// 调用方不感知迁移：ui.ts / api/core.ts 对这些符号原地 re-export，既有 import
// 零改动。新代码建议直接从本文件取。

import { AUD_EXTS, IMG_EXTS, VID_EXTS, extOfName } from './filekinds.ts'

export function fmtTs(ts: number | string): string {
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (isNaN(d.getTime())) return '-'
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export const ACTION_NAMES: Record<string, string> = {
  upload: '上传了', upload_instant: '秒传了', mkdir: '新建了目录', share: '创建了分享',
  share_download: '有人下载了分享', invite_redeemed: '加入了家庭空间',
  trash: '删除了', restore: '恢复了', move: '移动了', rename: '重命名了',
}

export function human(size: number | null | undefined): string {
  if (size == null) return '-'
  let s = size
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (s < 1024) return `${s.toFixed(s < 10 && unit !== 'B' ? 1 : 0)} ${unit}`
    s /= 1024
  }
  return `${s.toFixed(1)} PB`
}

// 媒体分类取自 filekinds 唯一事实源（9.9 复审 P3：两处手写列表已漂移）；
// pdf/压缩包/文档是图标专属分类，预览路由不用，留在这里
const NO_DOT = (a: string[]) => a.map(e => e.slice(1))
const VID_ICON = NO_DOT(VID_EXTS), AUD_ICON = NO_DOT(AUD_EXTS), IMG_ICON = NO_DOT(IMG_EXTS)

export function iconForName(name: string): string {
  const ext = extOfName(name).slice(1)
  if (VID_ICON.includes(ext)) return '🎬'
  if (AUD_ICON.includes(ext)) return '🎵'
  if (['zip', '7z', 'rar'].includes(ext)) return '🗜️'
  if (ext === 'pdf') return '📕'
  if (['doc', 'docx'].includes(ext)) return '📘'
  if (['xls', 'xlsx'].includes(ext)) return '📗'
  if (IMG_ICON.includes(ext)) return '🖼️'
  return '📄'
}

export function fmtSpeed(bps: number): string {
  if (!isFinite(bps) || bps <= 0) return '--'
  return bps >= 1024 * 1024 ? `${(bps / 1024 / 1024).toFixed(1)} MB/s` : `${Math.round(bps / 1024)} KB/s`
}

// API 路径编码：逐段 encodeURIComponent（保留 /），空段剔除（容忍重复斜杠）
export const enc = (p: string) => p.split('/').filter(Boolean).map(encodeURIComponent).join('/')
