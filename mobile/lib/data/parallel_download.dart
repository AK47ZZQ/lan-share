// 通用分片下载：把一个大文件切成 N 段 Range 并发拉，再流式拼回原文件。
//
// 原本只服务「更新包下载」（update_service._downloadParallel，2026-11-17 那轮
// review 打磨出来的）。2026-11-18 实测发现同一个收益对普通文件下载也成立：
//   CF 隧道单流 0.52 MB/s → 8 路并发 5.83 MB/s（11 倍）
//   FRP 隧道单流 1.35 MB/s → 8 路并发 1.28 MB/s（无收益，限的是总带宽）
// 所以抽出来给两边共用，并按文件大小决定分不分片。
//
// 关键防线（都是踩过坑加的，别删）：
// - **每片先验 206 + Content-Range 再落盘**：2026-11-18 review 指出原来用
//   dio.download 是「落盘完成后才返回」，服务器对 HEAD 说支持 Range、对 GET
//   回 200 时 8 片各写一份完整包（102MB × 8 = 816MB）才报错，低配机先 ENOSPC。
//   现在改成 dio.get<ResponseBody> 流式接收：先看状态码与 Content-Range，
//   确认无误才 openWrite。只验长度不够 —— 代理可能 clamp/对齐区间。
// - **CancelToken 全程透传**：暂停/删除任务必须能真的中断，否则用户点了暂停
//   而下载照跑完、还把状态覆盖成 done（2026-11-18 review P0）。
// - **一片失败就掐掉其余**：不然还要等另外 7 片各跑满 20 分钟才 rethrow。
// - **流式合并**：102MB 级别不能整块读进内存。
// - **先写 .tmp 再 rename**，且**长度校验在 rename 之前**：先 rename 再校验
//   的话，校验失败时目标路径上原有的好文件已经被覆盖销毁。
import 'dart:io';

import 'package:dio/dio.dart';

Future<void> downloadFileParallel(
  Dio dio,
  String url,
  String path, {
  void Function(int received, int total)? onProgress,
  int parts = 8,
  int minSizeForParallel = 10 << 20,
  CancelToken? cancelToken,
}) async {
  void checkCancelled() {
    if (cancelToken?.isCancelled ?? false) {
      throw DioException.requestCancelled(
          requestOptions: RequestOptions(path: url), reason: 'cancelled');
    }
  }

  var total = 0;
  var rangeOk = false;
  // 一致性锚：HEAD 时记下 ETag/Last-Modified，每片带 If-Range。
  // 没有它的话，文件在 HEAD 与 8 个 GET 之间被覆盖（家庭互传很常见，
  // 上传同名的 on_dup=overwrite 就是就地覆盖）会让部分片来自旧文件、
  // 部分来自新文件 —— 新旧大小相同时长度校验全过，合并出「大小正确、
  // 内容乱序」的坏文件（2026-11-18 review）。
  String? validator;
  try {
    final head = await dio.head(url, cancelToken: cancelToken);
    validator =
        head.headers.value('etag') ?? head.headers.value('last-modified');
    total =
        int.tryParse(head.headers.value(Headers.contentLengthHeader) ?? '') ??
        0;
    final ar = (head.headers.value('accept-ranges') ?? '').toLowerCase();
    // 太小的文件分片没有意义（额外 N 个连接 + 合并开销反而更慢）。
    // 阈值 10MB：CF 单流 0.52MB/s 时 10MB 要 19 秒，8 路只要 1.7 秒，
    // 值得；1MB 级别的握手就吃掉一半收益，不值（2026-11-18 实测）。
    rangeOk = ar.contains('bytes') && total > minSizeForParallel;
  } catch (_) {
    // HEAD 被拒（405）或被取消 → 走单流
    checkCancelled();
  }
  if (!rangeOk) {
    await dio.download(url, path,
        onReceiveProgress: onProgress, cancelToken: cancelToken);
    return;
  }

  final chunk = (total + parts - 1) ~/ parts;
  final temps = <File>[];
  final gotPerPart = List<int>.filled(parts, 0);
  var done = 0;
  onProgress?.call(0, total);

  // 一片失败就取消其余，别等 7 片各跑满 20 分钟
  final siblings = CancelToken();

  Future<void> one(int i) async {
    checkCancelled();
    // 每个分片独立 token：把「任务被取消」和「兄弟片失败」两条上游
    // 串联进来（dio 的 CancelToken 不支持直接传 List）
    final partToken = CancelToken();
    cancelToken?.whenCancel.then((_) {
      if (!partToken.isCancelled) partToken.cancel('task cancelled');
    });
    siblings.whenCancel.then((_) {
      if (!partToken.isCancelled) partToken.cancel('sibling failed');
    });
    final start = i * chunk;
    final end = (start + chunk - 1).clamp(0, total - 1);
    if (start > end) return;
    final tmp = File('$path.part$i');
    temps.add(tmp);
    final expect = end - start + 1;

    final resp = await dio.get<ResponseBody>(
      url,
      options: Options(
        headers: {
          'Range': 'bytes=$start-$end',
          // 文件在探测后被改动 → 服务器按 RFC 回 200 全量，
          // 而下面只接受 206，于是整批失败重来，不会拼出错位的包
          'If-Range': ?validator,
        },
        responseType: ResponseType.stream,
        receiveTimeout: const Duration(minutes: 20),
        // 只接受 206：200 表示服务器整包回了，绝不能落盘
        validateStatus: (s) => s == 206,
      ),
      cancelToken: partToken,
    );

    // 落盘前验 Content-Range：只验长度不够 —— 代理可能把区间 clamp 或对齐，
    // 长度对得上但起点错了，合并出来是静默损坏的文件（下载侧没有 sha 兜底）。
    final cr = resp.headers.value('content-range') ?? '';
    final m = RegExp(r'bytes\s+(\d+)-(\d+)/(\d+)').firstMatch(cr);
    if (m == null) {
      throw StateError('分片 $i 缺少合法的 Content-Range：$cr');
    }
    final crStart = int.parse(m.group(1)!);
    final crEnd = int.parse(m.group(2)!);
    final crTotal = int.parse(m.group(3)!);
    if (crStart != start || crEnd != end || crTotal != total) {
      throw StateError('分片 $i 区间不符'
          '（期望 $start-$end/$total，实得 $crStart-$crEnd/$crTotal）');
    }

    final sink = tmp.openWrite();
    try {
      await for (final data in resp.data!.stream) {
        checkCancelled();
        sink.add(data);
        // 单片贡献 clamp 到剩余空间：服务器多给也不能让总进度超过 total
        final room = expect - gotPerPart[i];
        gotPerPart[i] += data.length;
        done += data.length.clamp(0, room < 0 ? 0 : room);
        onProgress?.call(done.clamp(0, total), total);
      }
    } finally {
      await sink.flush();
      await sink.close();
    }

    final actual = await tmp.length();
    if (actual != expect) {
      throw StateError('分片 $i 长度不符（期望 $expect，实际 $actual）');
    }
  }

  try {
    await Future.wait([for (var i = 0; i < parts; i++) one(i)]);
  } catch (e) {
    if (!siblings.isCancelled) siblings.cancel('sibling failed');
    for (final t in temps) {
      try {
        await t.delete();
      } catch (_) {}
    }
    rethrow;
  }

  // 合并：流式拼，102MB 级不能整块读进内存（R2-P2 的 OOM 教训）。
  //
  // 先写 <path>.tmp 再 rename：同目录 rename 在 NTFS/ext4 上都是原子的。
  // **长度校验放在 rename 之前** —— 先 rename 再校验的话，校验失败时目标
  // 路径上原有的好文件已经被覆盖销毁，用户从「有一份可用的旧文件」退化成
  // 「什么都没有」（2026-11-18 review）。
  final tmpPath = '$path.tmp';
  final tmpFile = File(tmpPath);
  try {
    final sink = tmpFile.openWrite();
    try {
      for (final t in temps) {
        checkCancelled();
        await sink.addStream(t.openRead());
      }
      await sink.flush();
      await sink.close();
    } catch (e) {
      try {
        await sink.close();
      } catch (_) {}
      rethrow;
    }
    final got = await tmpFile.length();
    if (got != total) {
      throw StateError('分片下载合并后大小不符（期望 $total，实际 $got）');
    }
    await tmpFile.rename(path);
  } catch (e) {
    // 合并/校验任一环节失败：.tmp 和所有 .partN 一起清，不留半成品
    try {
      if (await tmpFile.exists()) await tmpFile.delete();
    } catch (_) {}
    for (final t in temps) {
      try {
        await t.delete();
      } catch (_) {}
    }
    rethrow;
  }

  for (final t in temps) {
    try {
      await t.delete();
    } catch (_) {}
  }
}
