// 应用级设置（App v0.6.0）：主题三档/应用锁/传输完成通知/文件页排序/常用目录。
// 全部非敏感，走 SharedPreferences；只在 UI isolate 读写（备份引擎的键
// 仍归 BackupConfig，互不掺和——跨 isolate 缓存坑见 0.4.6 修复批）。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AppSettings {
  final ThemeMode themeMode;
  final bool appLock;
  final bool notifyTransferDone;
  final String filesSort; // name | modified | size
  final bool filesAsc;
  final List<String> pinnedDirs; // 常用目录（相对路径，'' 根不可钉）

  const AppSettings({
    this.themeMode = ThemeMode.system,
    this.appLock = false,
    this.notifyTransferDone = true,
    this.filesSort = 'name',
    this.filesAsc = true,
    this.pinnedDirs = const [],
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
      appLock: sp.getBool('ui_app_lock') ?? false,
      notifyTransferDone: sp.getBool('notify_transfer_done') ?? true,
      filesSort: sp.getString('files_sort') ?? 'name',
      filesAsc: sp.getBool('files_asc') ?? true,
      pinnedDirs: sp.getStringList('files_pinned_dirs') ?? const [],
    );
  }

  Future<void> setThemeMode(ThemeMode m) async {
    state = _copy(themeMode: m);
    final sp = await SharedPreferences.getInstance();
    await sp.setString('ui_theme_mode', m.name);
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

  Future<void> togglePinnedDir(String path) async {
    final cur = List<String>.of(state.pinnedDirs);
    cur.contains(path) ? cur.remove(path) : cur.add(path);
    state = _copy(pinnedDirs: cur);
    final sp = await SharedPreferences.getInstance();
    await sp.setStringList('files_pinned_dirs', cur);
  }

  AppSettings _copy({ThemeMode? themeMode, bool? appLock, bool? notifyTransferDone,
      String? filesSort, bool? filesAsc, List<String>? pinnedDirs}) =>
      AppSettings(
          themeMode: themeMode ?? state.themeMode,
          appLock: appLock ?? state.appLock,
          notifyTransferDone: notifyTransferDone ?? state.notifyTransferDone,
          filesSort: filesSort ?? state.filesSort,
          filesAsc: filesAsc ?? state.filesAsc,
          pinnedDirs: pinnedDirs ?? state.pinnedDirs);
}

final settingsProvider =
    StateNotifierProvider<SettingsController, AppSettings>((_) => SettingsController());
