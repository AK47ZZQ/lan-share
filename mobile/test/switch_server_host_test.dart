// hostOfAddress 的判定边界。
//
// 这个函数决定「切换服务器时要不要清掉旧设备的 token」——
// 判成同一台 = 保留 token，判错就会把 A 服务器的 Bearer 凭据
// 发给 B 服务器（换部署、帮家人配、扫码接入别人的服务器都会撞上）。
//
// 取舍原则是「宁可多清一次」：多清的代价是重新登录一次；
// 漏清的代价是把自己的设备凭据交给第三方，还可能留在对方反代日志里。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/api/api_client.dart';

void main() {
  group('hostOfAddress', () {
    test('同一 IP 不同端口 = 同一台服务器（局域网地址 vs 隧道映射）', () {
      expect(
        hostOfAddress('https://192.0.2.10:8765'),
        hostOfAddress('https://192.0.2.10:18799'),
      );
    });

    test('同一域名不同端口 = 同一台服务器', () {
      expect(
        hostOfAddress('https://home.example.com:8765'),
        hostOfAddress('https://home.example.com:443'),
      );
    });

    test('不同 IP = 不同服务器', () {
      expect(
        hostOfAddress('https://192.0.2.10:8765'),
        isNot(hostOfAddress('https://192.0.2.20:8765')),
      );
    });

    test('不同域名 = 不同服务器', () {
      expect(
        hostOfAddress('https://a.example.com'),
        isNot(hostOfAddress('https://b.example.com')),
      );
    });

    test('局域网地址 vs 隧道域名 = 不同（token 必须清）', () {
      expect(
        hostOfAddress('https://192.0.2.10:8765'),
        isNot(hostOfAddress('https://cn-bj.frp.one:35123')),
      );
    });

    test('解析不出 host 时退回原串，两个不同串一定判为不同', () {
      expect(hostOfAddress('garbage'), 'garbage');
      expect(hostOfAddress('garbage'), isNot(hostOfAddress('other')));
    });

    test('尾部斜杠不影响判定', () {
      expect(
        hostOfAddress('https://192.0.2.10:8765/'),
        hostOfAddress('https://192.0.2.10:8765'),
      );
    });
  });
}
