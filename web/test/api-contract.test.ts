// Web↔服务端契约路径对齐守卫（web 测试批次，替代重量级 OpenAPI codegen 的
// 本仓方案——与 filekinds.test.ts 同一哲学：**把"要对齐"从注释变成可执行断言**）。
//
// 为什么不做 openapi-typescript 全量生成：web/src/api/types.ts 里一半是 UI 形状
// （列表项合并 token、视图模型字段），生成物会覆盖手写形状造成全站 churn；
// 而历史上真正咬过人的漂移是「手抄 URL/漏字段」（07/08 审查记录）。本测试钉住
// 其中可静态判定的一类：**web 前端引用的每个 /api/ 路径都必须存在于服务端
// 契约快照 contracts/openapi.json**（130 路径，server 端 test_contract.py 已保证
// 快照与实现一致，这里补上消费端的第三环）。
//
// 提取范围：web/src 下所有 .ts/.vue 文件里的 /api/ 字符串字面量（含模板字符串）。
// 规范化：${expr} 与 {param} 都归一为 {}，去 query、去尾斜杠后比对。

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, dirname } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = join(here, '..', 'src')
const openapi = JSON.parse(
  readFileSync(join(here, '..', '..', 'contracts', 'openapi.json'), 'utf-8'))

// {album_id} / ${albumId} → {}，便于两侧同构比对
const norm = (p: string) => p
  .replace(/\$\{[^}]+\}/g, '{}')
  .replace(/\{[^}]+\}/g, '{}')
  .split('?')[0]
  .replace(/\/+$/, '') || '/'

const serverPaths = new Set(Object.keys(openapi.paths).map(norm))

function listFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const full = join(dir, e.name)
    return e.isDirectory() ? listFiles(full) : (/\.(ts|vue)$/.test(e.name) ? [full] : [])
  })
}

function extractApiPaths(source: string): string[] {
  const out: string[] = []
  // 匹配 '/api/...' 与 `/api/...`（模板字符串里的 ${} 一并捕获到路径段里）
  const re = /['"`](\/api\/[^'"`?]*?)['"`]/g
  for (const m of source.matchAll(re)) out.push(m[1])
  return out
}

test('web 引用的每个 /api/ 路径都存在于契约快照', () => {
  const missing: string[] = []
  for (const f of listFiles(srcRoot)) {
    for (const raw of extractApiPaths(readFileSync(f, 'utf-8'))) {
      // 提取器会被模板字面量内嵌引号截断（如 `${link.split('?')}`），产生不带
      // 收口 `}` 的残串——这不是真实路径引用，跳过
      if (raw.includes('${') && !raw.includes('}')) continue
      const p = norm(raw)
      // 直接命中，或"字符串拼接出动态段"（如 '/api/upload/chunk/' + sid）补一段后命中
      if (serverPaths.has(p) || serverPaths.has(p + '/{}')) continue
      missing.push(`${p}  （${f} 里的 "${raw}"）`)
    }
  }
  assert.deepEqual(missing, [], `以下 web 引用的路径不在契约中（要么拼错/漂移，要么契约快照过期需重导出）:\n${missing.join('\n')}`)
})

test('契约里 web 已拆域模块应覆盖的核心域前缀抽查', () => {
  // 不是全量双向对齐（服务端不少端点本就不被 web 消费，如 App 专用），
  // 这里抽查 web 六个域模块的代表端点确实在契约中——防快照单侧改名。
  for (const p of ['/api/files', '/api/shares', '/api/photos', '/api/notifications',
    '/api/auth/me', '/api/admin/audit', '/api/upload/init', '/api/trash']) {
    assert.ok(serverPaths.has(norm(p)), `契约缺少 web 核心端点: ${p}`)
  }
})
