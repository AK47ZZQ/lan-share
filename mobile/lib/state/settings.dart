// 应用级设置（App v0.6.0）：主题三档/应用锁/传输完成通知/文件页排序/常用目录。
// 全部非敏感，走 SharedPreferences；只在 UI isolate 读写（备份引擎的键
// 仍归 BackupConfig，互不掺和——跨 isolate 缓存坑见 0.4.6 修复批）。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/auto_switch.dart';

class AppSettings {
  final ThemeMode themeMode;
  final bool amoledDark; // 暗色增强：AMOLED 纯黑底（0.8.4 起，独立于三档）
  final String accent; // 主题色板 id（Palettes.all）或 'auto'（跟随壁纸，0.8.5）
  final bool appLock;
  final bool notifyTransferDone;
  final String filesSort; // name | modified | size
  final bool filesAsc;
  final bool filesGrid; // 文件页布局：true=方形网格 / false=横向列表（v0.12，Web 同款）
  final List<String> pinnedDirs; // 常用目录（相对路径，'' 根不可钉）
  final bool autoSwitch; // 局域网/公网自动切换（v0.15；默认开）

  const AppSettings({
    this.themeMode = ThemeMode.system,
    this.amoledDark = false,
    this.accent = 'violet',
    this.appLock = false,
    this.notifyTransferDone = true,
    this.filesSort = 'name',
    this.filesAsc = true,
    this.filesGrid = false,
    this.pinnedDirs = const [],
    this.autoSwitch = true,
  });
}

class SettingsController extends StateNotifier<AppSettings> {
  SettingsController() : super(const AppSettings()) {
    _load();
  }

  Future<void> _load() async {
    final sp = await SharedPreferences.getInstance();
    state = AppSettings(
      themeMode: ThemeMode.values.firstWhere(
          (m) => m.name == (sp.getString('ui_theme_mode') ?? 'system'),
          orElse: () => ThemeMode.system),
      amoledDark: sp.getBool('ui_amoled_dark') ?? false,
      accent: sp.getString('ui_accent') ?? 'violet',
      appLock: sp.getBool('ui_app_lock') ?? false,
      notifyTransferDone: sp.getBool('notify_transfer_done') ?? true,
      filesSort: sp.getString('files_sort') ?? 'name',
      filesAsc: sp.getBool('files_asc') ?? true,
      filesGrid: sp.getBool('files_grid') ?? false,
      pinnedDirs: sp.getStringList('files_pinned_dirs') ?? const [],
      autoSwitch: sp.getBool('autoswitch_enabled') ?? true,
    );
  }

  Future<void> setThemeMode(ThemeMode m) async {
    state = _copy(themeMode: m);
    final sp = await SharedPreferences.getInstance();
    await sp.setString('ui_theme_mode', m.name);
  }

  Future<void> setAmoledDark(bool v) async {
    state = _copy(amoledDark: v);
    final sp = await SharedPreferences.getInstance();
    await sp.setBool('ui_amoled_dark', v);
  }

  /// 切主题色板（Palettes.all 的 id 或 'auto' 跟随壁纸）
  Future<void> setAccent(String v) async {
    state = _copy(accent: v);
    final sp = await SharedPreferences.getInstance();
    await sp.setString('ui_accent', v);
  }

  Future<void> setAppLock(bool v) async {
    state = _copy(appLock: v);
    final sp = await SharedPreferences.getInstance();
    await sp.setBool('ui_app_lock', v);
  }

  Future<void> setNotifyTransferDone(bool v) async {
    state = _copy(notifyTransferDone: v);
    final sp = await SharedPreferences.getInstance();
    await sp.setBool('notify_transfer_done', v);
  }

  Future<void> setFilesSort(String sort, {bool? asc}) async {
    state = _copy(filesSort: sort, filesAsc: asc ?? state.filesAsc);
    final sp = await SharedPreferences.getInstance();
    await sp.setString('files_sort', sort);
    await sp.setBool('files_asc', state.filesAsc);
  }

  /// 文件页布局切换（方形网格 / 横向列表），与排序同款持久化
  Future<void> setFilesGrid(bool v) async {
    state = _copy(filesGrid: v);
    final sp = await SharedPreferences.getInstance();
    await sp.setBool('files_grid', v);
  }

  /// 自动切换开关（v0.15）：与 AutoSwitch 服务共用 autoswitch_enabled 键，
  /// setter 里顺带启停服务，避免两处状态漂移
  Future<void> setAutoSwitch(bool v) async {
    state = _copy(autoSwitch: v);
    await AutoSwitch.I.setEnabled(v);
  }

  Future<void> togglePinnedDir(String path) async {
    final cur = List<String>.of(state.pinnedDirs);
    cur.contains(path) ? cur.remove(path) : cur.add(path);
    state = _copy(pinnedDirs: cur);
    final sp = await SharedPreferences.getInstance();
    await sp.setStringList('files_pinned_dirs', cur);
  }

  AppSettings _copy({ThemeMode? themeMode, bool? amoledDark, String? accent,
      bool? appLock, bool? notifyTransferDone, String? filesSort, bool? filesAsc,
      bool? filesGrid, List<String>? pinnedDirs, bool? autoSwitch}) =>
      AppSettings(
          themeMode: themeMode ?? state.themeMode,
          amoledDark: amoledDark ?? state.amoledDark,
          accent: accent ?? state.accent,
          appLock: appLock ?? state.appLock,
          notifyTransferDone: notifyTransferDone ?? state.notifyTransferDone,
          filesSort: filesSort ?? state.filesSort,
          filesAsc: filesAsc ?? state.filesAsc,
          filesGrid: filesGrid ?? state.filesGrid,
          pinnedDirs: pinnedDirs ?? state.pinnedDirs,
          autoSwitch: autoSwitch ?? state.autoSwitch);
}

final settingsProvider =
    StateNotifierProvider<SettingsController, AppSettings>((_) => SettingsController());

/// 应用锁的进程级解锁标记（不持久化）。false=冷启动待解锁；解锁成功、
/// 开启应用锁时的本人验证、锁屏"退出登录"逃生均置 true——同进程内
/// 登出重登不再立刻上锁（否则逃生口形同虚设，v0.6.0 恶性锁死教训）。
final sessionUnlockedProvider = StateProvider<bool>((ref) => false);

/// 生物识别弹窗在屏期间为 true。指纹弹窗在部分机型会把 Activity 打到
/// paused 再恢复，若无此标记会被误判成"从后台回来"而立刻重锁——
/// 0.6.0"开启即锁/解锁完又被锁回去"的根因。判定与开关验证共用一份。
final biometricBusyProvider = StateProvider<bool>((ref) => false);

/// 应用锁落锁判定（纯逻辑，单测覆盖）：把生命周期事件归类成"是否真的
/// 从后台回来"。只有 paused/hidden 算进过后台；inactive→resumed（指纹
/// 弹窗、权限框、下拉通知栏）不算——0.6.0 把任何 resumed 都当切后台，
/// 弹窗自己关闭就把刚解开的锁打回去，是恶性锁死的直接根因。
class LockGate {
  bool wasBackgrounded = false;

  /// 记录生命周期事件（resumed 由 shouldLockOnResume 消费并清标记）
  void onState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      wasBackgrounded = true;
    }
  }

  /// resumed 事件到达时调用：返回是否应落锁，并消费掉后台标记（不重复落锁）
  bool shouldLockOnResume({required bool biometricBusy}) {
    final fromBg = wasBackgrounded;
    wasBackgrounded = false;
    return fromBg && !biometricBusy;
  }
}
