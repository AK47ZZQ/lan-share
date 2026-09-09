// 文件类型分类的唯一事实源（9.9 复审 P3：原先 FileViewer 与 ui.ts 各维护一份
// 且已漂移——heic/heif 只在一边、doc/xls 只在另一边）。扩展名统一小写带点。
// 用途区分：这里的列表决定"预览路由"；ui.ts 的 emoji 图标在共享列表之上再
// 叠加图标专属分类（pdf/压缩包/文档），互不越界。
export const IMG_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.heic', '.heif']
export const VID_EXTS = ['.mp4', '.mkv', '.mov', '.webm', '.avi']
export const AUD_EXTS = ['.mp3', '.flac', '.wav', '.m4a', '.ogg']
export const DOCX_EXTS = ['.docx']
export const TEXT_EXTS = ['.txt', '.md', '.log', '.json', '.csv', '.py', '.js', '.ts', '.vue', '.css',
  '.html', '.xml', '.yml', '.yaml', '.ini', '.bat', '.sh', '.sql', '.toml']

/** 从文件名取小写带点扩展名（无扩展名返回空串） */
export function extOfName(name: string): string {
  const m = name.match(/(\.[^.]+)$/)
  return m ? m[1].toLowerCase() : ''
}
