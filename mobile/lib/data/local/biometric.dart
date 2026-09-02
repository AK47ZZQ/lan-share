// 生物识别门禁（local_auth）：一键登录前确认"机主在场"。
// biometricOnly=false——没录指纹但有锁屏 PIN 的设备也能用（都算本人在场）；
// 任何异常按不可用处理（入口隐藏，永不阻塞普通密码登录）。
import 'package:local_auth/local_auth.dart';

class Biometric {
  static final LocalAuthentication _auth = LocalAuthentication();

  /// 设备有"已录入"的生物识别（或有锁屏凭据）才显示一键登录入口。
  /// canCheckBiometrics 只代表有硬件——有硬件但未录入时 authenticate 必失败，
  /// 入口点了没反应是坏体验，故优先看已录入列表。
  static Future<bool> available() async {
    try {
      final enrolled = await _auth.getAvailableBiometrics();
      return enrolled.isNotEmpty || await _auth.isDeviceSupported();
    } catch (_) {
      return false;
    }
  }

  static Future<bool> authenticate(String reason) async {
    try {
      return await _auth.authenticate(
          localizedReason: reason,
          options: const AuthenticationOptions(
              biometricOnly: false, stickyAuth: true));
    } catch (_) {
      return false;
    }
  }
}
