/// 分片上传协议常量——客户端侧单一事实源（07 审查 P0 修复）。
///
/// 服务端 `app/config.py` 的 `MAX_CHUNK_SIZE`（64MB）是协议上限的唯一权威，
/// 并随 /api/upload/init、/api/upload/state 响应的 `max_chunk_size` 字段下发。
/// 客户端防御 clamp 必须引用这里的常量，不许再写死数字——历史上两引擎各写
/// `> 16MB 即拒绝`，服务端把 >5GB 文件的分片阶梯升到 32/64MB 后 App 上传
/// 必败（改 A 没改 B 的直接代价）。
library;

/// 协议最大分片（字节）。与服务端 MAX_CHUNK_SIZE 保持一致；契约由
/// 服务端测试 test_chunk_contract 钉住，联调时若不一致以服务端为准。
const int kMaxChunkSize = 64 * 1024 * 1024;

/// 校验服务端下发的分片大小：0/负值会让 total 计算抛 UnsupportedError；
/// 超过协议上限会把整个文件读进单请求内存 OOM（红队 P3 防御保留，
/// 但上限引用协议常量而非魔法数）。
bool isValidChunkSize(int chunkSize) {
  return chunkSize > 0 && chunkSize <= kMaxChunkSize;
}
