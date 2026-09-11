#!/usr/bin/env python3
"""UI 分层守卫：禁止 mobile/lib/ui/ 直接使用 dio。

背景：`项目记忆/07`（2026-09-10 审查）把「UI 层直发请求」判为 P1——data/api
层明明存在，却被 60+ 处页面绕过，导致后端契约一变更就要在十几个 UI 文件里
找散落的 URL 拼接与 JSON 取值（`项目记忆/03` 记着"契约缺陷是跨端共债"）。
本守卫把这个约定变成可执行断言，思路与后端已有的 AST 守卫一致。

为什么用「每文件违规数」做基线而不是「文件:行号」：
行号会随任何一次编辑漂移，基线会天天假红；计数只在你真的新增直发时变化。

用法：
    python scripts/check-ui-layering.py            # 校验（CI / check.bat）
    python scripts/check-ui-layering.py --init     # 重新生成基线（收编后下调）
"""
import os
import re
import sys

UI_ROOT = 'mobile/lib/ui'
BASELINE = 'scripts/ui_layering_baseline.txt'

RULES = [
    (re.compile(r"""import\s+['"]package:dio/dio\.dart['"]"""), "直接 import package:dio"),
    # 不要求带 ()：顺带拦"方法引用"与"点链带空格"两种绕过写法
    (re.compile(r'ApiClient\s*\.\s*I\s*\.\s*dio'), "调用 ApiClient.I.dio()"),
]


def scan():
    """返回 {相对路径: 违规处数}。

    路径守卫：本脚本用相对路径，**必须在仓库根目录运行**。早期版本在目录不存在
    时 os.walk 返回空、counts 也为空，于是"扫描到 0 处违规"被当成通过——
    静默假绿比直接报错危险得多。
    """
    if not os.path.isdir(UI_ROOT):
        raise SystemExit(f'✗ 找不到 {UI_ROOT}——请在仓库根目录运行本守卫（CI 与 check.bat 均满足）')

    counts = {}
    scanned = 0
    for dp, _dn, fn in os.walk(UI_ROOT):
        for f in fn:
            if not f.endswith('.dart'):
                continue
            scanned += 1
            p = os.path.join(dp, f).replace('\\', '/')
            n = 0
            with open(p, encoding='utf-8', errors='ignore') as fh:
                for line in fh:
                    if any(pat.search(line) for pat, _ in RULES):
                        n += 1
            if n:
                counts[p] = n
    if scanned == 0:
        raise SystemExit(f'✗ {UI_ROOT} 下没有任何 .dart 文件——路径配置可能已失效')
    return counts


def load_baseline():
    if not os.path.exists(BASELINE):
        return None
    base = {}
    with open(BASELINE, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) == 2:
                base[parts[0]] = int(parts[1])
    return base


def write_baseline(counts):
    lines = [
        '# UI 分层守卫基线（scripts/check-ui-layering.py）',
        '# 格式：<相对路径> <该文件允许的直发处数>',
        '#',
        '# 这批数字是「存量债」不是「许可」：收编一批就把对应数字下调，',
        '# 降到 0 即从本文件删除；新增直发会被守卫拦下（数字只准降不准升）。',
        '',
    ]
    for p in sorted(counts):
        lines.append(f'{p} {counts[p]}')
    lines.append('')
    with open(BASELINE, 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(lines))
    print(f'基线已写入 {BASELINE}（{len(counts)} 个文件，'
          f'{sum(counts.values())} 处存量）')


def main():
    counts = scan()
    if '--init' in sys.argv:
        write_baseline(counts)
        return 0

    base = load_baseline()
    if base is None:
        print(f'缺少基线文件 {BASELINE}；先跑 --init', file=sys.stderr)
        return 2

    total = sum(counts.values())
    base_total = sum(base.values())

    worse = {p: n for p, n in counts.items() if n > base.get(p, 0)}
    better = {p: n for p, n in base.items() if counts.get(p, 0) < n}
    fixed = [p for p in base if counts.get(p, 0) == 0]

    if worse:
        print('✗ UI 分层守卫失败：以下文件新增了直发请求', file=sys.stderr)
        for p in sorted(worse):
            print(f'    {p}: {base.get(p, 0)} -> {worse[p]}', file=sys.stderr)
        print('  正确做法：在 mobile/lib/data/api/ 下按域加方法，UI 只调语义方法。',
              file=sys.stderr)
        return 1

    print(f'✓ UI 分层守卫通过：存量 {total} 处 / 基线 {base_total} 处'
          f'（{len(counts)} 个文件）')
    if better:
        print('  提示：以下文件已比基线更少，可跑 --init 下调基线')
        for p in sorted(better):
            print(f'    {p}: {base[p]} -> {counts.get(p, 0)}')
    if fixed:
        print(f'  提示：{len(fixed)} 个文件已清零，可跑 --init 从基线移除')
    return 0


if __name__ == '__main__':
    sys.exit(main())
