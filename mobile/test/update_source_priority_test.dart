// 更新源优先级：版本相同时按国内可达性选源。
//
// 背景（2026-11-18 实机截图）：用户在 4G 下点「检查更新」，App 发现
// v4.1.7 (147) 并开始下载，界面提示「安装包从 GitHub 下载，国内直连
// 可能很慢」—— 卡在 8% 不动。
//
// 根因：三个源（服务端 / GitHub / R2）几乎总是返回同一个版本，而排序
// 只比 version 字符串，比较结果恒为 0；Dart 的 List.sort 不保证稳定，
// 于是可能选中 GitHub 那条，下载地址就变成 github.com。
//
// 现在 pickBest() 加了 tie-break：r2 > server > github。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';

UpdateInfo info(String source, String version,
        {int build = 147, String host = 'example.com'}) =>
    UpdateInfo(
      version: version,
      build: build,
      sha256: 'a' * 64,
      available: true,
      url: 'https://$host/app.apk',
      size: 1024,
      notes: '',
      source: source,
    );

void main() {
  group('pickBest：版本相同时按国内可达性选源', () {
    test('三源同版本 → 选 R2（自家域名，国内最快）', () {
      final best = UpdateService.pickBest([
        info('github', '4.1.7', host: 'github.com'),
        info('server', '4.1.7', host: 'lanshareapi.ak47zzq.com'),
        info('r2', '4.1.7', host: 'dl.ak47zzq.com'),
      ]);
      expect(best!.source, 'r2');
      expect(best.url, contains('dl.ak47zzq.com'));
    });

    test('没有 R2 时 → 选服务端，而不是 GitHub', () {
      final best = UpdateService.pickBest([
        info('github', '4.1.7', host: 'github.com'),
        info('server', '4.1.7'),
      ]);
      expect(best!.source, 'server');
    });

    test('只有 GitHub 时 → 仍然可用（不能因为优先级就把唯一源丢掉）', () {
      final best = UpdateService.pickBest([info('github', '4.1.7')]);
      expect(best!.source, 'github');
    });

    test('顺序无关：把 GitHub 放第一个也不能选中它', () {
      final best = UpdateService.pickBest([
        info('github', '4.1.7', host: 'github.com'),
        info('r2', '4.1.7', host: 'dl.ak47zzq.com'),
      ]);
      expect(best!.source, 'r2');
    });
  });

  group('pickBest：版本优先于源优先级', () {
    test('GitHub 版本更高 → 仍选 GitHub（新版比快更重要）', () {
      final best = UpdateService.pickBest([
        info('r2', '4.1.6', build: 146, host: 'dl.ak47zzq.com'),
        info('github', '4.1.7', build: 147, host: 'github.com'),
      ]);
      expect(best!.version, '4.1.7');
      expect(best.source, 'github');
    });

    test('build 号也参与比较（4.1.7+148 > 4.1.7+147）', () {
      final best = UpdateService.pickBest([
        info('r2', '4.1.7', build: 147),
        info('github', '4.1.7', build: 148),
      ]);
      expect(best!.build, 148);
    });
  });

  group('pickBest：边界', () {
    test('空列表 → null', () {
      expect(UpdateService.pickBest([]), isNull);
    });

    test('未知 source 排在最后（不抛异常）', () {
      final best = UpdateService.pickBest([
        info('custom', '4.1.7'),
        info('r2', '4.1.7'),
        info('mystery', '4.1.7'),
      ]);
      expect(best!.source, 'r2');
    });
  });
}
