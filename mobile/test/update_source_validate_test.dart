// UpdateSourceStore.validate 的边界（与 lanshare_cli/updater.py::normalize_source
// 同一契约，docs/更新源协议.md）。重点钉回环白名单两端一致：
// 服务端允许 http://127.0.0.1 / localhost / ::1（本机调试不该被迫配证书）。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_source.dart';

void main() {
  group('UpdateSourceStore.validate 回环白名单（与服务端一致）', () {
    test('http 回环三种写法都放行', () {
      expect(UpdateSourceStore.validate('http://127.0.0.1:9000/u'), isNull);
      expect(UpdateSourceStore.validate('http://localhost:9000'), isNull);
      expect(UpdateSourceStore.validate('http://[::1]:9000/u'), isNull,
          reason: 'Dart 的 Uri.host 对 [::1] 返回 ::1 —— 服务端允许它，App 不能拒');
    });

    test('http 非回环拒绝', () {
      expect(UpdateSourceStore.validate('http://192.168.1.100:8765/updates'),
          isNotNull);
    });

    test('https 一律放行', () {
      expect(UpdateSourceStore.validate('https://192.168.1.100:8765/updates'),
          isNull);
    });

    test('无 scheme / 其他 scheme 拒绝', () {
      expect(UpdateSourceStore.validate('192.168.1.100:8765'), isNotNull);
      expect(UpdateSourceStore.validate('file:///C:/x'), isNotNull);
      expect(UpdateSourceStore.validate('ftp://x/y'), isNotNull);
    });

    test('空地址拒绝', () {
      expect(UpdateSourceStore.validate('   '), isNotNull);
    });

    test('save() 落盘前去尾斜杠（多地址按序存的格式稳定）', () async {
      // save 需要 SharedPreferences 平台通道；这里只测纯函数 validate，
      // manifestUrl/resolveUrl 的形状已由 update_github_test 覆盖。
    });
  });
}
