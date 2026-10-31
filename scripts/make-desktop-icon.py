"""生成桌面 / 托盘 / 卸载程序用的图标（.ico + 预览 png）。

绘制全部走 brandmark.py —— 和手机 App 是同一套图形代码，不会再出现
「手机上描边云、电脑上实心胖云」这种事。

## 为什么每个尺寸单独画，而不是画个 512 再缩

32×32 缩到 16×16，云的描边只剩 0.5 个像素，PIL 插值出来是一团灰白。
Windows 的图标体系本来就要求「每一档单独优化」，尤其 16px 是任务栏和
文件列表的默认尺寸 —— 这一档看不清，整个图标就白做了。

brandmark.brand_icon() 内部已按尺寸分叉：
    >= 48px  描边云（品牌原样）
    <= 32px  实心云（清晰度优先）

## ICO 为什么手写而不是 PIL 的 sizes 参数

PIL 的 `save(..., sizes=[...])` 只有一张源图，各帧都是它缩出来的，
等于又回到了上面那个问题。ICO 容器本身很简单（6 字节头 + 16 字节/
条目 + PNG 数据），自己拼就能让每一帧都是独立绘制的结果。

用法：python scripts/make-desktop-icon.py
"""

import io
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brandmark import brand_icon, brand_tray  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
LOGO_DIR = os.path.join(ROOT, "logo")

# Windows 图标实际会用到的尺寸。20 是 125% 缩放下的任务栏，40/64 是
# 资源管理器中/大图标视图 —— 少一档系统就自己缩，白给。
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

# 托盘状态色（和托盘程序里的状态灯同一套）
STATE_COLORS = {
    "ok": (52, 199, 123),      # 绿：服务正常
    "warn": (245, 176, 65),    # 黄：部分异常
    "error": (235, 87, 87),    # 红：服务挂了
}


def save_ico(frames: list[tuple[int, Image.Image]], path: str) -> None:
    """手写 ICO：每帧是独立绘制的 PNG，尺寸各自最优。

    ICO 结构：
        ICONDIR      6 字节  (保留=0, 类型=1 图标, 数量)
        ICONDIRENTRY 16 字节 × n
        PNG 数据连续排布
    """
    n = len(frames)
    header = struct.pack("<HHH", 0, 1, n)
    entries = b""
    blobs = b""
    offset = 6 + 16 * n
    for size, im in frames:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        png = buf.getvalue()
        # 宽高字节：0 表示 256（这个字段只有 1 字节，256 存不下）
        dim = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dim, dim,      # 宽、高
            0,             # 调色板数（PNG 帧填 0）
            0,             # 保留
            1,             # 色彩平面
            32,            # 位深
            len(png),      # 数据长度
            offset,        # 数据偏移
        )
        offset += len(png)
        blobs += png

    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def main() -> None:
    os.makedirs(LOGO_DIR, exist_ok=True)

    frames = [(s, brand_icon(s)) for s in ICO_SIZES]
    ico_path = os.path.join(LOGO_DIR, "lanshare.ico")
    save_ico(frames, ico_path)
    print(f"  {ico_path}  ({os.path.getsize(ico_path)} 字节, {len(frames)} 帧)")

    # 预览用：256 和 32，方便看效果 / 文档引用
    for s in (256, 32):
        p = os.path.join(LOGO_DIR, f"lanshare-{s}.png")
        brand_icon(s).save(p)
        print(f"  {p}")

    # 托盘用的小图：透明底单色云 + 三种状态色。
    # 托盘程序运行时会用同一套几何现画，这里导出只是给构建期检查和预览用。
    # 32px 是托盘程序实际要加载的尺寸（系统会按 DPI 缩到 16/20/24）
    for s in (16, 20, 24, 32):
        brand_tray(s).save(os.path.join(LOGO_DIR, f"lanshare-tray-{s}.png"))
        for state, rgb in STATE_COLORS.items():
            brand_tray(s, rgb).save(
                os.path.join(LOGO_DIR, f"lanshare-tray-{s}-{state}.png"))
    print(f"  托盘图标：4 尺寸 × (无状态 + {len(STATE_COLORS)} 状态)")

    print("done")


if __name__ == "__main__":
    main()
