// 更新包目录「不经过 jni」的回归钉（2026-11-20）。
//
// 背景：path_provider 的 Android 实现自 2.3 起改走 `package:jni` 取
// `android.content.Context`。主人手机上这条 JNI 查类失败，抛
// `ClassNotFoundException: Landroid/content/Context;`，而它正是
// `download()` 的第一句 —— 表现是「点更新立刻报错、进度条一点没动」，
// 自动更新彻底不可用。
//
// 修法是把目录改问自家 Kotlin 通道（`lanshare/apk` → `documentsDir`，
// 返回 `filesDir/app_flutter`，与 path_provider 同源）。这里钉住三件事：
//   1. 通道可用时**只用通道**（单测里 path_provider 没实现，一旦回退就会抛）；
//   2. 落盘位置仍是 `<root>/Downloads/updates`（老包不搬家）；
//   3. 通道不可用时回退 path_provider（老原生层 / 非 Android 构建仍能用）。
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('lanshare/apk');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  void mockChannel(Future<Object?> Function(MethodCall call) handler) {
    messenger.setMockMethodCallHandler(channel, handler);
  }

  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  test('documentsRoot 优先走原生通道（通道成功即证明没碰 path_provider/jni）',
      () async {
    final calls = <String>[];
    final tmp = Directory.systemTemp.createTempSync('ls-upd-root-');
    addTearDown(() => tmp.deleteSync(recursive: true));
    mockChannel((call) async {
      calls.add(call.method);
      return tmp.path;
    });

    expect(await UpdateService.documentsRoot(), tmp.path);
    expect(calls, ['documentsDir']);
  });

  test('updateDir 仍是 <root>/Downloads/updates 且目录会被建出来', () async {
    final tmp = Directory.systemTemp.createTempSync('ls-upd-dir-');
    addTearDown(() => tmp.deleteSync(recursive: true));
    mockChannel((call) async => tmp.path);

    final dir = await UpdateService.updateDir();
    expect(dir.path, contains('Downloads'));
    expect(dir.path, endsWith('updates'));
    expect(dir.existsSync(), isTrue);
  });

  test('通道拿不到值时回退 path_provider（单测里表现为抛异常，说明真的回退了）',
      () async {
    mockChannel((call) async => null);
    // path_provider 在纯单测环境没有平台实现 → MissingPluginException
    await expectLater(UpdateService.documentsRoot(), throwsA(isA<Exception>()));
  });

  group('shortError 只留一句人话', () {
    test('平台异常的 Java 栈与 DexPathList 尾巴会被剪掉', () {
      final e = Exception(
        'Exception in Java: java.lang.ClassNotFoundException: Didn\'t find class '
        '"Landroid/content/Context;" on path: DexPathList[[zip file '
        '"/data/app/~~xxx==/base.apk"],nativeLibraryDirectories=[/system/lib64]]\n'
        'java.lang.ClassNotFoundException: ...\n'
        '\tat dalvik.system.BaseDexClassLoader.findClass(BaseDexClassLoader.java:259)',
      );
      final s = UpdateService.shortError(e);
      expect(s, contains('ClassNotFoundException'));
      expect(s, isNot(contains('DexPathList')));
      expect(s, isNot(contains('BaseDexClassLoader')));
      expect(s, isNot(contains('\n')));
      // `package:jni` 的前缀对用户没意义，不该出现在界面文案里
      expect(s, isNot(startsWith('Exception in Java')));
    });

    test('超长内容截断，空异常给兜底文案', () {
      expect(UpdateService.shortError('x' * 400).length, lessThanOrEqualTo(141));
      expect(UpdateService.shortError(''), '未知原因');
    });
  });
}
