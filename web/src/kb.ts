// 知识库共享工具（v0.24 M-K4）：wikilink 预处理、目标解析、分类色相。
// 与 App 端 mobile/lib/data/kb.dart 保持同一套规则（双端"同款"的根基）。

// [[目标]] / [[目标#锚]] / [[目标|别名]] / [[目标#锚|别名]]
const WIKILINK_RE = /\[\[([^\[\]|#\n]+?)(?:#[^\[\]\n]*)?(?:\|([^\[\]\n]+?))?\]\]/

const escHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** 把 [[目标]] 转成带 data-kblink 的内联 <a>（marked 放行内联 HTML，
 *  DOMPurify 默认保留 data-*；点击由 FileViewer 的事件委托统一处理）。
 *  label 做 HTML 转义——不依赖 DOMPurify 兜底才算真正的属性安全（红队 V8）。 */
export function preprocessWikilinks(md: string): string {
  return md.replace(new RegExp(WIKILINK_RE.source, 'g'), (_m, target: string, alias?: string) => {
    const t = target.trim()
    if (!t) return _m
    let label = (alias ?? t).trim().replace(/[\[\]]/g, '')
    if (!label) label = t
    return `<a href="#" data-kblink="${encodeURIComponent(t)}">${escHtml(label)}</a>`
  })
}

export interface KbName {
  name: string
  aliases: string[]
  path: string
  category: string
  ghost: boolean
}

const dirOf = (p: string): string => {
  const i = p.lastIndexOf('/')
  return i < 0 ? '' : p.substring(0, i)
}

const stemOf = (name: string): string => {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.substring(0, i) : name
}

/** 目标解析（与 server._resolve_target 严格镜像，红队 V5）：
 *  无斜杠目标走 aliases → 文件名（撞名：同目录 > 最短路 > casefold）；
 *  带路径目标只走相对路径（相对当前目录 → 相对根），不回退文件名/别名。
 *  返回 SHARE_ROOT 相对路径；未命中 null（ghost）。 */
export function resolveKbTarget(names: KbName[], target: string, currentDir: string): string | null {
  const t = target.trim().replace(/\\/g, '/')
  if (!t) return null
  const cf = (s: string) => s.toLowerCase()
  const tie = (a: KbName, b: KbName) => {
    const aSame = dirOf(a.path) === currentDir ? 0 : 1
    const bSame = dirOf(b.path) === currentDir ? 0 : 1
    if (aSame !== bSame) return aSame - bSame
    if (a.path.length !== b.path.length) return a.path.length - b.path.length
    return cf(a.path).localeCompare(cf(b.path))
  }
  if (!t.includes('/')) {
    const aliasHits = names.filter(n => n.path && (n.aliases ?? []).some(al => cf(al.trim()) === cf(t)))
    if (aliasHits.length) {
      aliasHits.sort(tie)
      return aliasHits[0].path
    }
    const lastSeg = t
    const byName = names.filter(n => n.path && cf(stemOf(n.name)) === cf(lastSeg))
    if (byName.length) {
      byName.sort(tie)
      return byName[0].path
    }
    return null
  }
  for (const cand of [currentDir ? `${currentDir}/${t}` : t, t]) {
    for (const n of names) {
      if (cf(n.path) === cf(cand)) return n.path
    }
  }
  return null
}

/** 分类色相（与 App 同式：hue = 类别字符串哈希 % 360） */
export function categoryHue(category: string): number {
  let h = 0
  for (const c of category) {
    h = (h * 31 + c.charCodeAt(0)) & 0x7fffffff
  }
  return (h % 3600) / 10
}
