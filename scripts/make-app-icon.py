# 生成安卓启动图标（与 web/public/icons 的 PWA 品牌一致：紫渐变 + 白 L）。
# 产出：
#   mipmap-*/ic_launcher.png          遗留图标（渐变全出血 + 圆角，API<26 与设置页兜底）
#   mipmap-*/ic_launcher_fg.png       自适应前景（仅白 L，透明底，108dp 画布）
#   mipmap-anydpi-v26/ic_launcher.xml 自适应装配（背景=@drawable/ic_launcher_bg 渐变）
# 用法：hermes venv python scripts/make-app-icon.py
# 几何与颜色按 web/public/icons/icon-512.png 实测（2026-09-01），改品牌先改这里。
from PIL import Image, ImageDraw
import os

RES = os.path.join(os.path.dirname(__file__), "..", "mobile", "android", "app", "src", "main", "res")
GRAD_TOP = (91, 107, 255)    # #5B6BFF
GRAD_BOT = (137, 91, 255)    # #895BFF
L_COLOR = (255, 255, 255)

# L 在 512 母版上的几何（bbox 居中放置）
L_BOX = (210, 163, 320, 349)   # x0,y0,x1,y1 总bbox（宽110 高186）
BAR_W = 41                      # 竖杆宽
FOOT_H = 34                     # 底脚高
CORNER = 90                     # 遗留图标圆角半径（512 母版）

# 密度 → (遗留 px[48dp], 自适应前景 px[108dp])
DENSITIES = {"mdpi": (48, 108), "hdpi": (72, 162), "xhdpi": (96, 216),
             "xxhdpi": (144, 324), "xxxhdpi": (192, 432)}


def draw_l(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """在 canvas 的 box 区域内画 L（竖杆左对齐 + 底脚全宽）。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    bar = BAR_W / 110 * w
    foot = FOOT_H / 186 * h
    d = ImageDraw.Draw(canvas)
    d.rectangle([x0, y0, x0 + bar - 1, y1 - 1], fill=L_COLOR)
    d.rectangle([x0, y1 - foot, x1 - 1, y1 - 1], fill=L_COLOR)


def rounded(im: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.size[0] - 1, im.size[1] - 1], radius, fill=255)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def gradient(size: int) -> Image.Image:
    top, bot = GRAD_TOP, GRAD_BOT
    col = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        col.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    return col.resize((size, size))


for dpi, (legacy_px, fg_px) in DENSITIES.items():
    d = os.path.join(RES, f"mipmap-{dpi}")
    os.makedirs(d, exist_ok=True)

    # 遗留：全出血渐变 + 白 L + 圆角
    legacy = gradient(legacy_px)
    # L bbox 占画布比例与 512 母版一致（110/512, 186/512），居中
    lw = round(110 / 512 * legacy_px); lh = round(186 / 512 * legacy_px)
    cx, cy = legacy_px // 2, legacy_px // 2
    draw_l(legacy, (cx - lw // 2, cy - lh // 2, cx - lw // 2 + lw, cy - lh // 2 + lh))
    rounded(legacy, round(CORNER / 512 * legacy_px)).save(os.path.join(d, "ic_launcher.png"))

    # 自适应前景：透明底 + 白 L（同比例，居中；安全区 66/108 充裕）
    fg = Image.new("RGBA", (fg_px, fg_px), (0, 0, 0, 0))
    lw = round(110 / 512 * fg_px); lh = round(186 / 512 * fg_px)
    cx, cy = fg_px // 2, fg_px // 2
    draw_l(fg, (cx - lw // 2, cy - lh // 2, cx - lw // 2 + lw, cy - lh // 2 + lh))
    fg.save(os.path.join(d, "ic_launcher_fg.png"))

ad = os.path.join(RES, "mipmap-anydpi-v26")
os.makedirs(ad, exist_ok=True)
with open(os.path.join(ad, "ic_launcher.xml"), "w") as f:
    f.write('<?xml version="1.0" encoding="utf-8"?>\n'
            '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@drawable/ic_launcher_bg"/>\n'
            '    <foreground android:drawable="@mipmap/ic_launcher_fg"/>\n'
            '    <monochrome android:drawable="@mipmap/ic_launcher_fg"/>\n'
            '</adaptive-icon>\n')

gd = os.path.join(RES, "drawable")
os.makedirs(gd, exist_ok=True)
with open(os.path.join(gd, "ic_launcher_bg.xml"), "w") as f:
    f.write('<?xml version="1.0" encoding="utf-8"?>\n'
            '<shape xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <gradient android:angle="270"\n'
            f'        android:startColor="#{GRAD_TOP[0]:02X}{GRAD_TOP[1]:02X}{GRAD_TOP[2]:02X}"\n'
            f'        android:endColor="#{GRAD_BOT[0]:02X}{GRAD_BOT[1]:02X}{GRAD_BOT[2]:02X}"/>\n'
            '</shape>\n')

print("icon assets written to", os.path.abspath(RES))
