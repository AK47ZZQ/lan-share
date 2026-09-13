// 批次 076（v0.28.7+81，bug·a6df 修复批）：目录选择器只留目录 /
// 服务端相册段不预编码。涉及网络的路径由 E2E 覆盖，这里只测可纯算部分。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/ui/dir_picker_page.dart';

void main() {
  group('dirEntriesFromItems（bug·a6df 根修：目录选择器只留目录）', () {
    final items = [
      {'name': '笔记', 'path': '笔记', 'is_dir': true, 'is_private_root': false},
      {
        'name': '7小时+提示词.md',
        'path': '笔记/7小时+提示词.md',
        'is_dir': false,
        'is_private_root': false
      },
      {
        'name': '🔒 私人空间',
        'path': 'private',
        'is_dir': true,
        'is_private_root': true
      },
      {
        'name': 'Lan share网盘相册',
        'path': '照片/user@example.com/Lan share网盘相册',
        'is_dir': true,
        'is_private_root': false
      },
    ];

    test('文件（is_dir=false）不进目录选择器——点不到就不会 400 卡死', () {
      final dirs = dirEntriesFromItems(items);
      expect(dirs.map((d) => d.name), ['Lan share网盘相册', '笔记', '🔒 私人空间']);
    });

    test('排序大小写不敏感（原行为保持）', () {
      final dirs = dirEntriesFromItems([
        {'name': 'beta', 'path': 'beta', 'is_dir': true},
        {'name': 'Alpha', 'path': 'alpha', 'is_dir': true},
      ]);
      expect(dirs.map((d) => d.name), ['Alpha', 'beta']);
    });

    test('is_dir 缺失视为文件剔除（服务端契约恒有该字段，防御性）', () {
      final dirs = dirEntriesFromItems([
        {'name': 'no-flag', 'path': 'no-flag'},
        {'name': 'ok', 'path': 'ok', 'is_dir': true},
      ]);
      expect(dirs.map((d) => d.name), ['ok']);
    });

    test('is_private_root 缺失时为 false', () {
      final dirs = dirEntriesFromItems([
        {'name': 'ok', 'path': 'ok', 'is_dir': true},
      ]);
      expect(dirs.single.isPrivateRoot, isFalse);
    });

    test('空目录返回空列表', () {
      expect(dirEntriesFromItems(const []), isEmpty);
    });
  });
}
