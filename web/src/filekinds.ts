// 文件类型分类的唯一事实源（9.9 复审 P3：原先 FileViewer 与 ui.ts 各维护一份
// 且已漂移——heic/heif 只在一边、doc/xls 只在另一边）。扩展名统一小写带点。
// 用途区分：这里的列表决定"预览路由"；ui.ts 的 emoji 图标在共享列表之上再
// 叠加图标专属分类（pdf/压缩包/文档），互不越界。
// 对齐基准是服务端 app/filetools.py 的 IMG_EXTS/VID_EXTS（07 审查 §5.1-3：
// 三端各持一份，改动需同步；服务端已补 .m4v/.3gp 缩略图能力）。
export const IMG_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.heic', '.heif']
export const VID_EXTS = ['.mp4', '.mkv', '.mov', '.m4v', '.3gp', '.webm', '.avi']
export const AUD_EXTS = ['.mp3', '.flac', '.wav', '.m4a', '.ogg', '.opus']
export const DOCX_EXTS = ['.docx']
export const TEXT_EXTS = ['.txt', '.md', '.log', '.json', '.csv', '.py', '.js', '.ts', '.vue', '.css',
  '.html', '.xml', '.yml', '.yaml', '.ini', '.bat', '.sh', '.sql', '.toml']

// 可请求 /thumb/ 的扩展名（由预览路由派生）：.svg 有意排除——浏览器直接导航
// svg 会执行内嵌脚本，服务端缩略图端点因此不收 svg（app/filetools.py 注释），
// 客户端请求只会白吃 404
export const THUMB_EXTS = [...IMG_EXTS.filter(e => e !== '.svg'), ...VID_EXTS]

/** 从文件名取小写带点扩展名（无扩展名返回空串） */
export function extOfName(name: string): string {
  const m = name.match(/(\.[^.]+)$/)
  return m ? m[1].toLowerCase() : ''
}
