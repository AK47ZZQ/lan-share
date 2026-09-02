// 备份/上传落位计算（0.7.0 重构，APP-DESIGN §8.14）：
//   图片备份 → 照片/<用户名>/<相册名>/
//   视频备份 → 视频/<用户名>/<相册名>/   （手机相册里的视频随相册走）
//   照片页手动上传（无相册上下文）→ 照片/<用户名>/手机上传/
// 相册名经安全化后必须是单段普通目录名，落位锁死在类型前缀内不逃逸。
// 改名/换账号：账本按账号隔离（引擎检测到账号变化自动清账本重对账），
// 秒传按内容 hash 免流量，新用户名目录即取即用；旧用户名目录留待管理员
// 手动清理。全部纯函数，单测覆盖（batch064）。

/// 手动上传专用"相册"（照片页选择器不返回来源相册）
const kManualUploadAlbum = '手机上传';

/// 相册名读不出来时的兜底
const kUncategorizedAlbum = '未分类';

/// 相册名安全化：单个合法目录名。
/// - 非法字符（Windows 保留集 + 路径分隔符）与控制符 → '_'
/// - 去首尾空白与点（Windows 目录名不以点/空格结尾）
/// - 空结果或纯点（. / ..）→ 未分类；超长截断 60 字符
String safeBucketName(String raw) {
  var name = raw.trim();
  name = name.replaceAll(RegExp(r'[\\/:*?"<>|\x00-\x1f\x7f]'), '_');
  // 首尾空白与点都去：Windows 目录名尾点/尾空格非法；纯点串由此变空
  name = name.replaceAll(RegExp(r'^[.\s]+|[.\s]+$'), '');
  if (name.isEmpty) return kUncategorizedAlbum;
  if (name.length > 60) name = name.substring(0, 60);
  return name;
}

/// 自动备份目标目录：按媒体类型分总夹，第二层用户名，第三层相册名
String backupDestDir({
  required String username,
  required String kind, // image | video
  required String bucket,
}) {
  final top = kind == 'video' ? '视频' : '照片';
  return '$top/$username/${safeBucketName(bucket)}';
}
