// Web 纯函数单元测试（web 测试批次 D2）。
//
// 被测对象 = src/pure.ts（fmtTs/human/iconForName/fmtSpeed/enc，vue-free）与
// src/activity.ts（动态文案/跳转，type-only import 同样 vue-free）。
// 这批函数是"每个视图都在用、坏了却没人立刻发现"的底座：字节格式化差一个
// 1024、路径编码漏一段、动态跳转指错目录——UI 上只表现为"数字怪怪的"。
// 这里把当前行为钉住：**断言的是现状契约**，改行为必须连测试一起改。
//
// 跑法：npm test（node --test，零依赖零网络，node 原生 TS 类型剥离）。

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { fmtTs, human, iconForName, fmtSpeed, enc } from '../src/pure.ts'
import { who, baseName, activityTitle, relTime, activityRoute } from '../src/activity.ts'
import type { ActivityItem } from '../src/api/types.ts'

// ── human：字节人性化 ────────────────────────────────────────
test('human 空/零/边界', () => {
  assert.equal(human(null), '-')
  assert.equal(human(undefined), '-')
  assert.equal(human(0), '0 B')
  assert.equal(human(1023), '1023 B')
})
test('human 各档位（<10 带一位小数，≥10 取整）', () => {
  assert.equal(human(1024), '1.0 KB')
  assert.equal(human(5 * 1024), '5.0 KB')
  assert.equal(human(10 * 1024), '10 KB')
  assert.equal(human(5 * 1024 * 1024), '5.0 MB')
  assert.equal(human(1024 ** 3), '1.0 GB')
  assert.equal(human(1024 ** 4), '1.0 TB')
})

// ── fmtTs / fmtSpeed ────────────────────────────────────────
test('fmtTs 秒级时间戳 → 本地 M-DD HH:MM；坏输入 → -', () => {
  const ts = new Date(2026, 8, 11, 13, 5).getTime() / 1000
  assert.equal(fmtTs(ts), '9-11 13:05')
  assert.equal(fmtTs('not-a-date'), '-')
})
test('fmtSpeed 非法输入 --，KB/MB 两档', () => {
  assert.equal(fmtSpeed(0), '--')
  assert.equal(fmtSpeed(-5), '--')
  assert.equal(fmtSpeed(NaN), '--')
  assert.equal(fmtSpeed(Infinity), '--')
  assert.equal(fmtSpeed(512), '1 KB/s')
  assert.equal(fmtSpeed(2048), '2 KB/s')
  assert.equal(fmtSpeed(1024 * 1024), '1.0 MB/s')
  assert.equal(fmtSpeed(2.5 * 1024 * 1024), '2.5 MB/s')
})

// ── iconForName：与 filekinds 事实源联动 ─────────────────────
test('iconForName 按扩展名分类', () => {
  assert.equal(iconForName('a.jpg'), '🖼️')
  assert.equal(iconForName('b.mp4'), '🎬')
  assert.equal(iconForName('c.mp3'), '🎵')
  assert.equal(iconForName('d.zip'), '🗜️')
  assert.equal(iconForName('e.pdf'), '📕')
  assert.equal(iconForName('f.docx'), '📘')
  assert.equal(iconForName('g.xlsx'), '📗')
  assert.equal(iconForName('h.txt'), '📄')
  assert.equal(iconForName('i.unknownext'), '📄')
})

// ── enc：API 路径逐段编码 ────────────────────────────────────
test('enc 逐段编码、剔空段、保留斜杠', () => {
  assert.equal(enc('/a/b/c.txt'), 'a/b/c.txt')
  assert.equal(enc('a//b'), 'a/b')
  assert.equal(enc('/docs/'), 'docs')
  assert.equal(enc('/我的 照片/图 1.jpg'), encodeURIComponent('我的 照片') + '/' + encodeURIComponent('图 1.jpg'))
  assert.equal(enc('/a/b+c/d'), 'a/b%2Bc/d')
})

// ── activity.ts：动态文案与跳转 ──────────────────────────────
const item = (over: Partial<ActivityItem>): ActivityItem => ({
  ts: 0, username: 'u', action: 'upload', target: 'a/b.jpg', detail: '', count: 1, ...over,
})

test('who 有用户名/链接访客/早期记录', () => {
  assert.equal(who(item({ username: 'mom' })), 'mom')
  assert.equal(who(item({ username: '', action: 'share_download' })), '链接访客')
  assert.equal(who(item({ username: '', action: 'upload' })), '早期记录')
})
test('baseName 取末段、容忍斜杠冗余', () => {
  assert.equal(baseName('a/b/c.txt'), 'c.txt')
  assert.equal(baseName('/x/'), 'x')
  assert.equal(baseName('plain'), 'plain')
})
test('activityTitle 聚合/定向分享/普通三形态', () => {
  assert.equal(activityTitle(item({ count: 3 })), 'u 上传了 3 项')
  assert.equal(
    activityTitle(item({ action: 'share_target', target: 'docs/报告.pdf', detail: '给妈妈' })),
    'u 定向分享了 报告.pdf 给妈妈')
  assert.equal(activityTitle(item({ action: 'trash', target: '旧文件.zip' })), 'u 删除了 旧文件.zip')
})
test('relTime 相对时间四档', () => {
  const now = Date.now() / 1000
  assert.equal(relTime(now - 30), '刚刚')
  assert.equal(relTime(now - 300), '5 分钟前')
  assert.equal(relTime(now - 3 * 3600), '3 小时前')
  assert.equal(relTime(now - 2 * 86400 - 60), '2 天前')
})
test('activityRoute 按动作直达', () => {
  assert.deepEqual(activityRoute(item({ action: 'upload', target: 'a/b/c.jpg' })),
    { path: '/', query: { dir: 'a/b' } })
  assert.deepEqual(activityRoute(item({ action: 'upload', target: 'c.jpg' })),
    { path: '/' })
  assert.deepEqual(activityRoute(item({ action: 'mkdir', target: 'a/b' })),
    { path: '/', query: { dir: 'a/b' } })
  assert.deepEqual(activityRoute(item({ action: 'trash' })), { path: '/trash' })
  assert.deepEqual(activityRoute(item({ action: 'share' })), { path: '/shares' })
  assert.equal(activityRoute(item({ action: 'mystery' })), null)
})
