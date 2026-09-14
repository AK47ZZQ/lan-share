"""缩略图后台预热（20260914213301-31cd）纯函数测试。

预热键公式必须与 /thumb 端点逐字节一致——错一位就是整轮 CPU 白烧且永远
补不齐（键 = md5(f"{rel}:{mtime_ns}:{size}")，与 previews.thumb_file 同源）；
视频按轮限额（ffmpeg 最重）；非媒体扩展名跳过；batch 截断。
"""

import hashlib

from app.services.thumb_warmup import pick_missing


def test_pick_missing_generates_endpoint_key(tmp_path):
    rows = [("照片/a.jpg", 111, 222)]
    got = pick_missing(rows, tmp_path)
    assert len(got) == 1
    rel, cached = got[0]
    assert rel == "照片/a.jpg"
    key = hashlib.md5("照片/a.jpg:111:222".encode()).hexdigest()
    assert cached == tmp_path / f"{key}.jpg"
    # 与端点同键：文件生成后下一轮不再挑中（不重复烧 CPU）
    cached.write_bytes(b"x")
    assert pick_missing(rows, tmp_path) == []


def test_pick_missing_video_cap_and_non_media(tmp_path):
    rows = [
        ("照片/a.jpg", 1, 1),
        ("视频/v1.mp4", 2, 2),
        ("视频/v2.mp4", 3, 3),
        ("视频/v3.mp4", 4, 4),   # 超出 video_cap
        ("照片/b.txt", 5, 5),    # 非媒体：跳过
        ("照片/c", 6, 6),        # 无扩展名：跳过
    ]
    got = pick_missing(rows, tmp_path, batch=10, video_cap=2)
    assert {r for r, _ in got} == {"照片/a.jpg", "视频/v1.mp4", "视频/v2.mp4"}


def test_pick_missing_batch_limit(tmp_path):
    rows = [(f"照片/x{i}.jpg", i, i) for i in range(20)]
    assert len(pick_missing(rows, tmp_path, batch=8)) == 8
