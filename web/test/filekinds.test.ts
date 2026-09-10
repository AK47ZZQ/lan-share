// Web 端最小单元测试（11 号复审批次 3，P2-7）。
//
// 背景：Web 是四端里**唯一零测试**的一端（服务端 210 项、移动端 179 项），
// 而 `api.ts`/`uploader.ts` 是 bug 高发区。
//
// 这份测试用 **Node 内置 test runner + 内置 assert**——零依赖、零网络、零构建，
// 所以它能在任何环境（含离线）跑起来。先覆盖最有价值的一类：
// `filekinds.ts` 是与**服务端 `app/filetools.py`** 及移动端对齐的扩展名事实源，
// 历史上正是"三端各持一份、改一处漏两处"漂移过（07/08 审查记录的漂移事故）。
// 这里把"要对齐"从注释变成**可执行断言**。

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { IMG_EXTS, VID_EXTS, THUMB_EXTS, extOfName } from '../src/filekinds.ts'

const SERVER_FILETOOLS = fileURLToPath(
  new URL('../../app/filetools.py', import.meta.url))

/** 从 Python 源码里抠出 `NAME = {...}` 的字符串集合 */
function pythonExtSet(source: string, name: string): Set<string> {
  const m = source.match(new RegExp(`${name}\\s*=\\s*\\{([^}]*)\\}`))
  assert.ok(m, `服务端 filetools.py 里找不到 ${name} —— 契约可能已改名`)
  return new Set([...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]))
}

test('extOfName：取小写带点扩展名', () => {
  assert.equal(extOfName('photo.JPG'), '.jpg')
  assert.equal(extOfName('archive.tar.gz'), '.gz')
  assert.equal(extOfName('README'), '')
  assert.equal(extOfName('.gitignore'), '.gitignore')
  assert.equal(extOfName(''), '')
})

test('THUMB_EXTS 排除 .svg（浏览器直导航 svg 会执行内嵌脚本）', () => {
  assert.ok(THUMB_EXTS.includes('.jpg'))
  assert.ok(THUMB_EXTS.includes('.mp4'))
  assert.ok(!THUMB_EXTS.includes('.svg'), 'svg 必须留在预览路由之外')
})

test('THUMB_EXTS 由 IMG/VID 派生且无重复', () => {
  assert.equal(THUMB_EXTS.length, new Set(THUMB_EXTS).size, '存在重复项')
  for (const e of THUMB_EXTS) {
    assert.ok(IMG_EXTS.includes(e) || VID_EXTS.includes(e), `${e} 不在 IMG/VID 中`)
  }
})

test('契约：VID_EXTS 与服务端 app/filetools.py 完全一致', () => {
  const server = pythonExtSet(readFileSync(SERVER_FILETOOLS, 'utf8'), 'VID_EXTS')
  const client = new Set(VID_EXTS)
  assert.deepEqual([...client].sort(), [...server].sort(),
    '视频扩展名三端漂移：改 filekinds.ts 时须同步 app/filetools.py / media_kinds.dart')
})

test('契约：服务端 IMG_EXTS 是客户端子集，唯一差集为 .svg', () => {
  const server = pythonExtSet(readFileSync(SERVER_FILETOOLS, 'utf8'), 'IMG_EXTS')
  const client = new Set(IMG_EXTS)
  const onlyClient = [...client].filter(e => !server.has(e)).sort()
  const onlyServer = [...server].filter(e => !client.has(e)).sort()
  assert.deepEqual(onlyServer, [], '服务端有而前端没有的图片扩展名 → 前端会当附件下载')
  assert.deepEqual(onlyClient, ['.svg'],
    '前端 IMG_EXTS 相对服务端只允许 .svg 一项差异（服务端刻意不收 svg）')
})

test('所有扩展名统一为小写带点格式', () => {
  for (const e of [...IMG_EXTS, ...VID_EXTS, ...THUMB_EXTS]) {
    assert.match(e, /^\.[a-z0-9]+$/, `${e} 格式不合法（应为小写带点）`)
  }
})
