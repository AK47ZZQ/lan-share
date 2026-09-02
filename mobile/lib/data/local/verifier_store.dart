// 验证器条目持久化：SecureStore 双写（Keystore + SP 沙盒副本）。
// 取舍：TOTP secret 与登录密码档同敏感级，但**可靠性优先**——secret 读不出
// = 无法生成动态码 = 一步都登不进（恢复码是服务器侧的最后兜底），沿用密码档
// 的双写先例（Keystore 冷启动解密失败有 SP 兜底，见 M-A1 教训）。
import 'dart:convert';

import '../totp.dart';
import 'secure_store.dart';

class VerifierStore {
  static const _key = 'totp_entries';

  static Future<List<TotpEntry>> load() async {
    try {
      final raw = await SecureStore.string(_key);
      if (raw == null || raw.isEmpty) return [];
      final list = (jsonDecode(raw) as List)
          .map((e) => TotpEntry.fromJson(e as Map<String, dynamic>))
          .toList();
      return list;
    } catch (_) {
      return []; // 数据损坏视为未绑定（可重新扫码）
    }
  }

  static Future<void> save(List<TotpEntry> entries) async {
    final raw = jsonEncode([for (final e in entries) e.toJson()]);
    await SecureStore.setString(_key, raw);
  }

  /// 绑定（同 label 覆盖 = 同账号重复绑定不产生僵尸条目，与设备 Token 同思路）。
  static Future<void> upsert(TotpEntry entry) async {
    final list = await load();
    list.removeWhere((e) => e.label == entry.label);
    list.add(entry);
    await save(list);
  }

  static Future<void> remove(String label) async {
    final list = await load();
    list.removeWhere((e) => e.label == label);
    await save(list);
  }

  /// 找指定账号名的条目（登录页一键回填用）。
  static Future<TotpEntry?> findByLabel(String label) async {
    final list = await load();
    for (final e in list) {
      if (e.label == label) return e;
    }
    return null;
  }
}
