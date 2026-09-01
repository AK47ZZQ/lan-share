// 会话状态：登录（+TOTP）→ 会话换设备 Token → 身份确认（APP-DESIGN §3）。
// 服务端契约：/api/login 密码正确但启用 TOTP 时返回 totp_required 不发会话；
// /api/login/totp {username,password,code} 换会话；随后 /api/auth/token 用
// 该会话（Cookie）换长效 Bearer Token，Cookie 用完即弃。
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../data/api/auth_cache.dart';
import '../data/api/cookies.dart';
import '../data/local/prefs.dart';
import '../data/local/secure_store.dart';

class SessionState {
  final bool checking;   // 请求进行中
  final bool loggedIn;
  final String? username;
  final String? role;
  final String? error;
  final bool needTotp;   // 服务端要求动态验证码
  final TrustRequiredError? trustIssue; // 自签证书待确认
  const SessionState(
      {this.checking = false, this.loggedIn = false, this.username,
      this.role, this.error, this.needTotp = false, this.trustIssue});
}

class SessionController extends StateNotifier<SessionState> {
  String _username = '';
  String _password = '';
  bool _busy = false;    // 登录流程互斥（连点/双入口防重入）

  SessionController() : super(const SessionState(checking: true));

  /// 启动探测：本地有 Token 则用 /api/auth/me 确认（失效会被拦截器清掉）。
  /// 网络失败不清 Token——临时不可达不应抹掉本地凭据。
  Future<void> bootstrap() async {
    final token = await SecureStore.token();
    if (token == null) {
      state = const SessionState();
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/auth/me',
          options: Options(headers: {'x-login-flow': '1'}));
      final name = r.data['username'] as String?;
      await Prefs.setUsername(name ?? ''); // 备份引擎（后台 isolate）要用
      state = SessionState(
          loggedIn: true,
          username: name,
          role: r.data['role'] as String?);
    } on DioException catch (e) {
      if (e.response?.statusCode != 401) {
        // 非凭据问题（服务器不可达等）：不假装登出，给出提示
        state = const SessionState(error: '服务器暂时不可达，请检查网络后重试');
        return;
      }
      state = const SessionState();
    } catch (_) {
      state = const SessionState();
    }
  }

  /// 第一步：密码登录（启用 TOTP 的账号会转入 needTotp 状态等验证码）
  Future<void> login({
    required String server,
    required String username,
    required String password,
  }) async {
    if (_busy) return;
    _busy = true;
    _username = username;
    _password = password;
    state = const SessionState(checking: true);
    try {
      await ApiClient.I.switchServer(server);
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/login',
          options: Options(headers: {'x-login-flow': '1'}),
          data: {'username': username, 'password': password});
      if (r.data['totp_required'] == true) {
        state = const SessionState(needTotp: true);
        return;
      }
      await _exchangeToken(dio, r);
    } on DioException catch (e) {
      state = SessionState(error: _errMsg(e), trustIssue: _trustOf(e));
    } catch (e) {
      state = SessionState(error: _brief(e));
    } finally {
      _busy = false;
    }
  }

  /// 第二步：TOTP 动态码换会话（仅 needTotp 后调用）。
  /// server 由 UI 传入当前地址框值——防止用户在 TOTP 等待期改地址后仍打旧地址。
  Future<void> loginTotp(String code, {required String server}) async {
    if (_busy) return;
    _busy = true;
    state = const SessionState(checking: true, needTotp: true);
    try {
      await ApiClient.I.switchServer(server);
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/login/totp',
          options: Options(headers: {'x-login-flow': '1'}),
          data: {'username': _username, 'password': _password, 'code': code});
      await _exchangeToken(dio, r);
    } on DioException catch (e) {
      state = SessionState(error: _errMsg(e), needTotp: true, trustIssue: _trustOf(e));
    } catch (e) {
      state = SessionState(error: _brief(e), needTotp: true);
    } finally {
      _busy = false;
    }
  }

  /// 登录会话 Cookie 只用这一次：立即换长效设备 Token（M-A0 契约）
  Future<void> _exchangeToken(Dio dio, Response r) async {
    final cookie = cookieHeaderFrom(r.headers['set-cookie']);
    final deviceName = await Prefs.deviceName();
    final tr = await dio.post('/api/auth/token',
        options: Options(headers: {'Cookie': cookie, 'x-login-flow': '1'}),
        data: {'name': deviceName});
    await SecureStore.saveToken(tr.data['token'] as String);

    final me = await dio.get('/api/auth/me',
        options: Options(headers: {'x-login-flow': '1'}));
    final name = me.data['username'] as String?;
    await Prefs.setUsername(name ?? ''); // 备份引擎（后台 isolate）要用
    state = SessionState(
        loggedIn: true,
        username: name,
        role: me.data['role'] as String?);
  }

  static TrustRequiredError? _trustOf(DioException e) {
    return e.error is TrustRequiredError ? e.error as TrustRequiredError : null;
  }

  static String _errMsg(DioException e) {
    final d = e.response?.data;
    if (d is Map && d['detail'] != null) return d['detail'].toString();
    if (e.error is TrustRequiredError) return '服务器证书需要确认';
    final status = e.response?.statusCode;
    if (status != null) return '服务端返回 HTTP $status';
    return '连接失败，请检查服务器地址与网络';
  }

  /// 登录任何一步失败都把具体原因带上屏（方便远程排障）
  static String _brief(Object e) {
    final s = e.toString();
    return s.length > 200 ? s.substring(0, 200) : s;
  }

  Future<void> logout() async {
    _username = '';
    _password = '';
    await SecureStore.clearToken();
    await Prefs.setUsername('');
    // 换账号后缩略图缓存必须清空（缓存 key 不含账号，防跨账号残留）
    await AuthCacheManager.I.emptyCache();
    state = const SessionState();
  }
}

final sessionProvider =
    StateNotifierProvider<SessionController, SessionState>((ref) {
  final c = SessionController();
  c.bootstrap();
  return c;
});
