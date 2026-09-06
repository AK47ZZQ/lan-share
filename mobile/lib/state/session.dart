// 会话状态：登录（+TOTP）→ 会话换设备 Token → 身份确认（APP-DESIGN §3）。
// 服务端契约：/api/login 密码正确但启用 TOTP 时返回 totp_required 不发会话；
// /api/login/totp {username,password,code} 换会话；随后 /api/auth/token 用
// 该会话（Cookie）换长效 Bearer Token，Cookie 用完即弃。
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../data/auto_switch.dart';
import '../data/api/auth_cache.dart';
import '../data/api/cookies.dart';
import '../data/local/prefs.dart';
import '../data/local/saved_credential.dart';
import '../data/local/secure_store.dart';

class SessionState {
  final bool checking;   // 请求进行中
  final bool loggedIn;
  final String? username;
  final String? nickname; // 显示层昵称（v0.17）：登录/路径仍用 username
  final String? avatarPath; // 服务端相对路径（.avatars/<uid>.jpg），空=无头像
  final String? role;
  /// 昵称优先显示：各界面展示身份时统一走这里
  String? get displayName =>
      ((nickname ?? '').trim().isNotEmpty ? nickname : username);
  final String? error;
  final bool needTotp;   // 服务端要求动态验证码
  final TrustRequiredError? trustIssue; // 自签证书待确认
  const SessionState(
      {this.checking = false, this.loggedIn = false, this.username,
      this.nickname, this.avatarPath, this.role, this.error,
      this.needTotp = false, this.trustIssue});
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
          nickname: r.data['nickname'] as String?,
          avatarPath: r.data['avatar_path'] as String?,
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
      AutoSwitch.I.noteManualPick(); // 登录时选的入口粘性 10 分钟（防自动切回）
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
      AutoSwitch.I.noteManualPick();
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

  /// 从配对码等模式切回密码输入：清掉"等待动态码"状态重新开始
  void resetTotp() {
    if (!_busy) state = const SessionState();
  }

  /// 配对码登录（免账号密码）：Web 端已登录会话生成一次性 6 位码，
  /// 此处 /api/pair/claim 直接换长效设备 Token。凭据规则与密码登录一致。
  Future<void> loginByPair(String code, {required String server}) async {
    if (_busy) return;
    _busy = true;
    state = const SessionState(checking: true);
    try {
      await ApiClient.I.switchServer(server);
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/pair/claim',
          options: Options(headers: {'x-login-flow': '1'}),
          data: {'code': code.trim(), 'name': await Prefs.deviceName()});
      await SecureStore.saveToken(r.data['token'] as String);
      final me = await dio.get('/api/auth/me',
          options: Options(headers: {'x-login-flow': '1'}));
      final name = me.data['username'] as String?;
      await Prefs.setUsername(name ?? '');
      state = SessionState(
          loggedIn: true,
          username: name,
          nickname: me.data['nickname'] as String?,
          avatarPath: me.data['avatar_path'] as String?,
          role: me.data['role'] as String?);
    } on DioException catch (e) {
      state = SessionState(error: _errMsg(e), trustIssue: _trustOf(e));
    } catch (e) {
      state = SessionState(error: _brief(e));
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
    // Token 已签发：立即清内存明文密码（R2-P3-7）。TOTP 二步路径（110 行）
    // 未走此处之前还需要密码重试，语义不受影响
    _password = '';
    state = SessionState(
        loggedIn: true,
        username: name,
        nickname: me.data['nickname'] as String?,
        role: me.data['role'] as String?);
  }

  /// 资料更新（v0.17）：改昵称后原地刷新状态，免重新拉 /me
  Future<void> updateProfile(String nickname) async {
    final dio = await ApiClient.I.dio();
    final r = await dio.patch('/api/auth/profile',
        data: {'nickname': nickname});
    final nick = (r.data['nickname'] as String?) ?? '';
    state = SessionState(
        loggedIn: state.loggedIn,
        username: state.username,
        nickname: nick,
        avatarPath: state.avatarPath,
        role: state.role);
  }

  /// 头像上传后重取 /me：同步 avatarPath（昵称/角色一起以服务端为准）
  Future<void> refreshFromMe() async {
    final dio = await ApiClient.I.dio();
    final me = await dio.get('/api/auth/me',
        options: Options(headers: {'x-login-flow': '1'}));
    state = SessionState(
        loggedIn: state.loggedIn,
        username: me.data['username'] as String?,
        nickname: me.data['nickname'] as String?,
        avatarPath: me.data['avatar_path'] as String?,
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

  /// 服务端改名后同步本机状态（0.8.0 我的页改用户名）：
  /// 设备 Token 不变、会话保持；prefs 用户名缓存由调用方更新
  void applyRename(String newName) {
    _username = newName;
    state = SessionState(
        loggedIn: true, username: newName, role: state.role);
  }

  Future<void> logout() async {
    _username = '';
    _password = '';
    await SecureStore.clearToken();
    await Prefs.setUsername('');
    // 主动退出 = 要求彻底退出：生物识别一键登录的凭据存档一并清除
    // （Token 自然失效的掉线场景不清，那种情况正需要一键重登）
    await SavedCredentialStore.clear();
    // 换账号后缩略图缓存必须清空（缓存 key 不含账号，防跨账号残留）
    await AuthCacheManager.I.emptyCache();
    await AutoSwitch.I.reset(); // 上一会话的自动切换痕迹一并清掉
    // 失败负缓存同上：账号 A 无权看被记 404 的 URL，B 可能有权
    AuthCacheManager.I.clearFailureCache();
    state = const SessionState();
  }
}

final sessionProvider =
    StateNotifierProvider<SessionController, SessionState>((ref) {
  final c = SessionController();
  c.bootstrap();
  return c;
});
