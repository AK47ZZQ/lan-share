/// 媒体扩展名单一事实源（07 架构审查 §5.1-3 / §4.1-2）。
///
/// 历史漂移：files_page 无 .m4v（文件页不识别为视频、传输页识别）、
/// photos_page 无 3gp、各页各写一份。对齐基准：服务端 app/filetools.py
/// 的 IMG_EXTS/VID_EXTS（.svg 有意不收，见服务端注释）与 web filekinds.ts。
/// 改动需三端同步。
library;

/// 图片扩展名（小写带点）
const Set<String> kImgExts = {
  '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif',
};

/// 视频扩展名（小写带点）
const Set<String> kVidExts = {
  '.mp4', '.mov', '.m4v', '.3gp', '.webm', '.avi', '.mkv',
};

/// 音频扩展名（小写带点）
const Set<String> kAudExts = {
  '.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.opus',
};

/// 无点变体：FilePicker 的 f.extension 口径（不带点）
final Set<String> kVidExtsNoDot =
    kVidExts.map((e) => e.substring(1)).toSet();

/// 文件名 → 小写带点扩展名；无扩展名（或点是首字符）返回空串
String extOfName(String name) {
  final i = name.lastIndexOf('.');
  if (i <= 0 || i == name.length - 1) return '';
  return name.substring(i).toLowerCase();
}
