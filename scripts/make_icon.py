"""
生成 LAN Share 的应用图标（.ico + .png）。

品牌取自 logo/app-login-brand.svg：
  紫蓝渐变背景 #5B6CFF → #895BFF（左上 → 右下）
  半透明白色圆角块
  白色云朵（Material Symbols `cloud` 的近似形状）

为什么手画而不是转 SVG：cairosvg 依赖 libcairo，Windows 上装不了；
svglib/reportlab 也没装。原图是 24×24 的矢量路径，形状简单，
用「三个圆 + 一个矩形」就能还原到肉眼无差别。
"""

from PIL import Image, ImageDraw

# 品牌色
GRAD_FROM = (0x5B, 0x6C, 0xFF)
GRAD_TO = (0x89, 0x5B, 0xFF)

# 超采样倍数：先按 4 倍画再缩小，边缘才平滑
SS = 4


def gradient_image(size: int) -> Image.Image:
    """斜向渐变：左上 #5B6CFF → 右下 #895BFF。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            # 对角线插值，比纯纵向更像原图
            t = (x + y) / (2.0 * (size - 1)) if size > 1 else 0.0
            px[x, y] = (
                int(GRAD_FROM[0] + (GRAD_TO[0] - GRAD_FROM[0]) * t),
                int(GRAD_FROM[1] + (GRAD_TO[1] - GRAD_FROM[1]) * t),
                int(GRAD_FROM[2] + (GRAD_TO[2] - GRAD_FROM[2]) * t),
            )
    return img


def rounded_mask(size: int, radius_ratio: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255
    )
    return mask


def draw_cloud(base: Image.Image) -> Image.Image:
    """在 24×24 设计坐标系里画云朵，再按比例缩放到实际尺寸。

    形状要求：三团圆弧的上半部 + 一条平直底边，四个图形的**底边必须在同一条线上**，
    否则右下角会露出直角缺口（第一版就是这么翻车的）。
    """
    S = base.width
    k = S / 24.0
    cloud = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(cloud)

    def box(x0, y0, x1, y1):
        return [int(x0 * k), int(y0 * k), int(x1 * k), int(y1 * k)]

    white = (255, 255, 255, 255)
    BOTTOM = 16.4          # 所有图形的共同底边

    # 左团（最低最矮）、中团（最大最高）、右团（中等）
    d.ellipse(box(4.4, 9.2, 11.6, BOTTOM), fill=white)
    d.ellipse(box(7.0, 5.2, 17.2, BOTTOM), fill=white)
    d.ellipse(box(12.4, 8.6, 19.6, BOTTOM), fill=white)
    # 用矩形把三团之间和底部填平 —— 底边与三个椭圆完全重合，不会留缺口
    d.rectangle(box(7.0, 11.0, 17.2, BOTTOM), fill=white)

    return Image.alpha_composite(base, cloud)


def make_icon(size: int) -> Image.Image:
    S = size * SS

    # 1) 渐变底 + 圆角裁剪
    grad = gradient_image(S).convert("RGBA")
    grad.putalpha(rounded_mask(S, 0.1875))

    # 2) 半透明白色圆角块：比云朵大一圈，露出淡白色边（原图 opacity 0.24）
    block = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pad = int(S * 0.145)
    ImageDraw.Draw(block).rounded_rectangle(
        [pad, pad, S - pad, S - pad],
        radius=int(S * 0.105),
        fill=(255, 255, 255, 61),
    )
    icon = Image.alpha_composite(grad, block)

    # 3) 云朵
    icon = draw_cloud(icon)

    return icon.resize((size, size), Image.LANCZOS)


def main():
    out_dir = "logo"
    sizes = [16, 24, 32, 48, 64, 128, 256]

    # 主图：256px PNG（安装包/README 可用）
    big = make_icon(256)
    big.save(f"{out_dir}/lanshare-256.png")
    print(f"  logo/lanshare-256.png  {big.size}")

    # 托盘用的 32px PNG（WinForms 里 Icon 更好控制）
    make_icon(32).save(f"{out_dir}/lanshare-32.png")
    print("  logo/lanshare-32.png   (32, 32)")

    # 多尺寸 ICO：只传 sizes，PIL 会自己从主图缩放。
    # 传 append_images 会让 sizes 失效 —— 第一版 ico 只有 633 字节就是这个坑。
    make_icon(256).save(f"{out_dir}/lanshare.ico", format="ICO",
                        sizes=[(s, s) for s in sizes])
    print(f"  logo/lanshare.ico      含 {len(sizes)} 个尺寸: {sizes}")


if __name__ == "__main__":
    main()
