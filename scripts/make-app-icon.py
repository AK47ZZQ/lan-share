# 生成启动/品牌图标（品牌：登录页同款云朵 + 渐变底，2026-09-04 起替代"白 L"）。
# 产出：
#   mipmap-*/ic_launcher.png          遗留图标（渐变全出血 + 白云 + 圆角，API<26 与设置页兜底）
#   mipmap-*/ic_launcher_fg.png       自适应前景（仅白云，透明底，108dp 画布）
#   mipmap-anydpi-v26/ic_launcher.xml 自适应装配（背景=@drawable/ic_launcher_bg 渐变，
#                                     monochrome 复用前景白云轮廓）
#   web/public/icons + web/dist/icons PWA 三件（192/512/apple-touch，同一 512 母版缩放）
# 云朵取自 Material Icons "cloud"（24×24 viewBox 填充外形）：内置 mini SVG 路径
# 解析器（M/C/c/h/z + 隐式重复）+ 三次贝塞尔拉直为多边形，不引 SVG 依赖。
# 用法：hermes venv python scripts/make-app-icon.py；改品牌先改这里。
from PIL import Image, ImageDraw
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
RES = os.path.join(ROOT, "mobile", "android", "app", "src", "main", "res")
PWA_DIRS = [os.path.join(ROOT, "web", "public", "icons"),
            os.path.join(ROOT, "web", "dist", "icons")]
GRAD_TOP = (91, 107, 255)    # #5B6BFF（登录页渐变起点）
GRAD_BOT = (137, 91, 255)    # #895BFF（渐变终点）
CLOUD = (255, 255, 255)

# Material Icons "cloud" 填充外形（单条子路径；outlined 版的内孔不需要）
CLOUD_PATH = ("M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 "
              "2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 "
              "0-2.64-2.05-4.78-4.65-4.96z")

CORNER = 90          # 遗留图标圆角半径（512 母版）
CLOUD_FRAC = 0.50    # 云朵宽占画布边长比例（用户反馈 0.58 偏大；自适应安全区 66%）

# 密度 → (遗留 px[48dp], 自适应前景 px[108dp])
DENSITIES = {"mdpi": (48, 108), "hdpi": (72, 162), "xhdpi": (96, 216),
             "xxhdpi": (144, 324), "xxxhdpi": (192, 432)}


def _flatten_cubic(p0, p1, p2, p3, n=16):
    """三次贝塞尔 → n 段折线点列（不含起点）。"""
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def parse_path(d: str):
    """mini SVG 路径解析：支持 M m L l H h V v C c Z z 与命令隐式重复。
    返回子路径列表（每条是绝对坐标折线，闭合处补回起点）。"""
    parts = re.findall(r"[MmLlHhVvCcZz]|-?\d+(?:\.\d+)?(?:e-?\d+)?", d)
    i, pos, start = 0, (0.0, 0.0), (0.0, 0.0)
    cur, subs, prev = [], [], ""

    def num():
        nonlocal i
        v = float(parts[i])
        i += 1
        return v

    while i < len(parts):
        tok = parts[i]
        cmd = tok if re.match(r"[A-Za-z]", tok) else prev  # 数字=隐式重复上一命令
        if re.match(r"[A-Za-z]", tok):
            i += 1
        prev = cmd
        if cmd == "M":
            pos = (num(), num())
            start = pos
            cur = [pos]
            prev = "L"  # M 后随数字按 L 处理
        elif cmd == "m":
            pos = (pos[0] + num(), pos[1] + num())
            start = pos
            cur = [pos]
            prev = "l"
        elif cmd == "L":
            pos = (num(), num())
            cur.append(pos)
        elif cmd == "l":
            pos = (pos[0] + num(), pos[1] + num())
            cur.append(pos)
        elif cmd in "Hh":
            dx = num()
            pos = (dx if cmd == "H" else pos[0] + dx, pos[1])
            cur.append(pos)
        elif cmd in "Vv":
            dy = num()
            pos = (pos[0], dy if cmd == "V" else pos[1] + dy)
            cur.append(pos)
        elif cmd == "C":
            p1, p2, p3 = (num(), num()), (num(), num()), (num(), num())
            cur.extend(_flatten_cubic(pos, p1, p2, p3))
            pos = p3
        elif cmd == "c":
            p1 = (pos[0] + num(), pos[1] + num())
            p2 = (pos[0] + num(), pos[1] + num())
            p3 = (pos[0] + num(), pos[1] + num())
            cur.extend(_flatten_cubic(pos, p1, p2, p3))
            pos = p3
        elif cmd in "Zz":
            if cur:
                cur.append(start)
                subs.append(cur)
                cur = []
            pos = start
    if cur:
        subs.append(cur)
    return subs


def draw_cloud(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """把云朵外形等比缩放画进 canvas 的 box（正方形，云朵垂直居中）。"""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    scale = side / 24.0
    ox = (x0 + x1) / 2 - 12 * scale
    oy = (y0 + y1) / 2 - 12 * scale  # 路径 bbox y∈[4,20]，中心即 12
    poly = [(x * scale + ox, y * scale + oy) for (x, y) in parse_path(CLOUD_PATH)[0]]
    ImageDraw.Draw(canvas).polygon(poly, fill=CLOUD)


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


def cloud_on(size: int, frac: float, bg_image: Image.Image | None = None) -> Image.Image:
    """size 见方的画布，居中画 frac 比例的白云；bg_image=None 为透明底。"""
    im = bg_image.convert("RGBA") if bg_image is not None \
        else Image.new("RGBA", (size, size), (0, 0, 0, 0))
    w = round(frac * size)
    cx = cy = size // 2
    draw_cloud(im, (cx - w // 2, cy - w // 2, cx - w // 2 + w, cy - w // 2 + w))
    return im


for dpi, (legacy_px, fg_px) in DENSITIES.items():
    d = os.path.join(RES, f"mipmap-{dpi}")
    os.makedirs(d, exist_ok=True)

    # 遗留：全出血渐变 + 白云 + 圆角
    legacy = cloud_on(legacy_px, CLOUD_FRAC, bg_image=gradient(legacy_px))
    rounded(legacy, round(CORNER / 512 * legacy_px)).save(os.path.join(d, "ic_launcher.png"))

    # 自适应前景：透明底 + 白云（安全区 66/108，0.58 充裕）
    cloud_on(fg_px, CLOUD_FRAC).save(os.path.join(d, "ic_launcher_fg.png"))

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

# PWA 三件：512 母版 + 192 + apple-touch(180)，与安卓同一品牌
for out_dir in PWA_DIRS:
    os.makedirs(out_dir, exist_ok=True)
    cloud_on(512, CLOUD_FRAC, bg_image=gradient(512)).save(
        os.path.join(out_dir, "icon-512.png"))
    cloud_on(512, CLOUD_FRAC, bg_image=gradient(512)).resize(
        (192, 192), Image.LANCZOS).save(os.path.join(out_dir, "icon-192.png"))
    cloud_on(512, CLOUD_FRAC, bg_image=gradient(512)).resize(
        (180, 180), Image.LANCZOS).save(os.path.join(out_dir, "apple-touch-icon.png"))

print("icon assets written:", os.path.abspath(RES), "+ PWA icons")
