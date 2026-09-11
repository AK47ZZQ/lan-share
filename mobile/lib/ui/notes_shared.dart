// 笔记列表页与编辑器共享的 prefs key（自 notes_page.dart 顶层私有成员公共化）：
// 历史上它们跨两个页面类使用导致无法按类拆文件（评价报告 P1 遗留项）。
// key 的字符串值与公共化前逐字一致，老用户 prefs 不受影响。

const kNotesLastDir = 'notes_last_dir';
String draftKey(String remotePath) => 'note_draft_$remotePath';
