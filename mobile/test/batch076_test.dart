// v0.24 知识库（M-K1/K2）纯逻辑测试：补全上下文、wikilink 预处理、
// 目标解析（客户端镜像服务端顺序）、frontmatter 维护、图谱过滤。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/kb.dart';

void main() {
  group('completionContextAt', () {
    test('光标在未闭合 [[ 后给出上下文', () {
      final ctx = completionContextAt('今天读 [[费曼', 8);
      expect(ctx, isNotNull);
      expect(ctx!.start, 4);
      expect(ctx.partial, '费曼');
    });
    test('已闭合 ]] 不在补全态', () {
      expect(completionContextAt('[[费曼]]', 7), isNull);
    });
    test('partial 里出现换行不补全', () {
      expect(completionContextAt('[[abc\ndef', 9), isNull);
    });
    test('嵌套 [[ 取最近一层', () {
      final ctx = completionContextAt('[[a [[b', 7);
      expect(ctx, isNotNull);
      expect(ctx!.partial, 'b');
    });
    test('空文本无上下文', () {
      expect(completionContextAt('', 0), isNull);
    });
  });

  group('applyCompletion', () {
    test('替换 [[partial 为 [[choice]] 且光标落到 ]] 后', () {
      const text = '读 [[费曼 学习一下';
      final ctx = completionContextAt(text, 6)!; // 光标在“费曼”之后
      expect(ctx.partial, '费曼');
      final (newText, caret) = applyCompletion(text, ctx, '费曼学习法');
      expect(newText, '读 [[费曼学习法]] 学习一下');
      expect(caret, '读 [[费曼学习法]]'.length);
    });
    test('含目录的目标保持原样（服务端按路径解析）', () {
      const text = '见 [[学';
      final ctx = completionContextAt(text, text.length)!;
      final (newText, _) = applyCompletion(text, ctx, '学习/输出倒逼输入');
      expect(newText, '见 [[学习/输出倒逼输入]]');
    });
  });

  group('preprocessWikilinks', () {
    test('基础 [[目标]] 转伪协议链接', () {
      final out = preprocessWikilinks('看 [[费曼学习法]] 这篇');
      expect(out, '看 [费曼学习法](kblink:%E8%B4%B9%E6%9B%BC%E5%AD%A6%E4%B9%A0%E6%B3%95) 这篇');
    });
    test('别名与锚点', () {
      expect(preprocessWikilinks('[[a b|别 名]]'), '[别 名](kblink:a%20b)');
      expect(preprocessWikilinks('[[note#小节]]'), contains('](kblink:note)'));
    });
    test('label 里的方括号被清理（防截断 md 链接）', () {
      final out = preprocessWikilinks('[[t|别[名]]]'.replaceAll(']]]', ']]'));
      expect(out.contains('['), isTrue);
      expect(out.contains('[别 名]'), isFalse);
    });
    test('空目标原样保留', () {
      expect(preprocessWikilinks('[[]]'), '[[]]');
    });
  });

  group('kblinkTarget', () {
    test('roundtrip', () {
      const t = '学习/输出倒逼输入';
      final href = 'kblink:${Uri.encodeComponent(t)}';
      expect(kblinkTarget(href), t);
    });
    test('非 kblink 返回 null', () {
      expect(kblinkTarget('https://x.com'), isNull);
      expect(kblinkTarget(null), isNull);
    });
  });

  group('resolveLinkTarget', () {
    final names = <Map<String, dynamic>>[
      {'name': 'Feynman.md', 'aliases': ['费曼技巧'], 'path': 'kb/方法/Feynman.md', 'category': '方法'},
      {'name': 'MOC.md', 'aliases': <String>[], 'path': 'kb/a/MOC.md', 'category': 'a'},
      {'name': 'MOC.md', 'aliases': <String>[], 'path': 'kb/b/c/MOC.md', 'category': 'b/c'},
    ];
    test('别名精确命中（casefold）', () {
      expect(resolveLinkTarget(names, '费曼技巧', ''), 'kb/方法/Feynman.md');
      expect(resolveLinkTarget(names, 'FEYNMAN 技巧', ''), isNull);
    });
    test('文件名命中大小写不敏感', () {
      expect(resolveLinkTarget(names, 'feynman', ''), 'kb/方法/Feynman.md');
    });
    test('撞名：同目录优先，其次更浅路径', () {
      expect(resolveLinkTarget(names, 'MOC', 'a'), 'kb/a/MOC.md');
      expect(resolveLinkTarget(names, 'MOC', 'z'), 'kb/a/MOC.md');
    });
    test('相对路径命中', () {
      expect(resolveLinkTarget(names, '方法/Feynman.md', 'kb'), 'kb/方法/Feynman.md');
    });
    test('带路径未命中不回退文件名（红队 V5：与服务端一致）', () {
      expect(resolveLinkTarget(names, '不存在目录/Feynman', 'kb'), isNull);
      expect(resolveLinkTarget(names, '不存在目录/Feynman', ''), isNull);
    });
    test('未命中返回 null（ghost）', () {
      expect(resolveLinkTarget(names, '不存在的笔记', ''), isNull);
    });
  });

  group('touchFrontmatter', () {
    test('无 frontmatter 时补 created/updated', () {
      final out = touchFrontmatter('正文', now: DateTime(2026, 9, 10));
      expect(out, startsWith('---\ncreated: 2026-09-10\nupdated: 2026-09-10\n---'));
      expect(out.endsWith('正文'), isTrue);
    });
    test('已有 frontmatter：updated 刷新、created 保留、其他字段不动', () {
      const src = '---\ntitle: T\ncreated: 2020-01-01\ntags: [a]\n---\n正文';
      final out = touchFrontmatter(src, now: DateTime(2026, 9, 10));
      expect(out, contains('created: 2020-01-01'));
      expect(out, contains('updated: 2026-09-10'));
      expect(out, contains('tags: [a]'));
      expect(out.endsWith('正文'), isTrue);
    });
    test('frontmatter 有 created 缺 updated 时只补 updated', () {
      const src = '---\ncreated: 2020-01-01\n---\nX';
      final out = touchFrontmatter(src, now: DateTime(2026, 9, 10));
      expect(out, contains('updated: 2026-09-10'));
      expect(out, contains('created: 2020-01-01'));
    });
    test('未闭合 frontmatter 不动（避免吃正文）', () {
      const src = '---\ntitle: 没闭合\n正文也算 frontmatter？';
      expect(touchFrontmatter(src), src);
    });
  });

  group('KbGraph / KbFilter', () {
    KbGraph buildGraph() => KbGraph.fromJson({
          'nodes': [
            {'id': 'g/a.md', 'path': 'g/a.md', 'name': 'a.md', 'category': '', 'tags': ['t1'], 'aliases': [], 'out': 1, 'ind': 0, 'ghost': false, 'external': false, 'orphan': false, 'summary': 'S', 'x': 1.0, 'y': 2.0},
            {'id': 'g/b.md', 'path': 'g/b.md', 'name': 'b.md', 'category': '子', 'tags': [], 'aliases': ['别名'], 'out': 0, 'ind': 1, 'ghost': false, 'external': false, 'orphan': false, 'summary': '', 'x': 3.0, 'y': 4.0},
            {'id': 'g/孤.md', 'path': 'g/孤.md', 'name': '孤.md', 'category': '', 'tags': [], 'aliases': [], 'out': 0, 'ind': 0, 'ghost': false, 'external': false, 'orphan': true, 'summary': '', 'x': 0, 'y': 0},
            {'id': 'ghost:欠', 'path': '', 'name': '欠', 'category': '', 'tags': [], 'aliases': [], 'out': 0, 'ind': 1, 'ghost': true, 'external': false, 'orphan': false, 'summary': '', 'x': 0, 'y': 0},
          ],
          'edges': [
            ['g/a.md', 'g/b.md', 2],
            ['g/a.md', 'ghost:欠', 1],
          ],
          'stats': {'nodes': 4, 'edges': 2, 'ghosts': 1, 'orphans': 1},
        });

    test('fromJson 解析节点/边权重/统计', () {
      final g = buildGraph();
      expect(g.nodes.length, 4);
      expect(g.edges[0].weight, 2);
      expect(g.ghosts, 1);
      expect(g.byId['g/a.md']!.x, 1.0);
      expect(g.adj['g/a.md']!.length, 2); // 无向邻接
    });

    test('分类过滤含子分类前缀', () {
      final f = KbFilter()..category = '子';
      expect(f.matchIds(buildGraph()), {'g/b.md'});
    });

    test('标签/查询（含别名）/隐藏开关/只看幽灵', () {
      final g = buildGraph();
      expect((KbFilter()..tag = 't1').matchIds(g), {'g/a.md'});
      expect((KbFilter()..query = '别').matchIds(g), {'g/b.md'});
      expect((KbFilter()..hideOrphans = true).matchIds(g).contains('g/孤.md'), isFalse);
      expect((KbFilter()..hideGhosts = true).matchIds(g).contains('ghost:欠'), isFalse);
      expect((KbFilter()..ghostsOnly = true).matchIds(g), {'ghost:欠'});
    });

    test('relaxStep 对悬空邻接不抛（指向被过滤节点）', () {
      final g = buildGraph();
      final vis = g.nodes.where((n) => n.id == 'g/a.md').toList();
      relaxStep(vis, g.adj); // a 的邻居 b/ghost 不在 vis 里
      expect(vis[0].x, 1.0); // 无有效邻居 → 位置不变
    });
  });
}
