# 生成启动/品牌图标（品牌：登录页同款——渐变底 + 半透明白圆角块 + 描边云朵，
# 2026-09-04 按用户拍板与 App 内品牌头对齐；此前经历"白 L"→实心云两版）。
# 产出：
#   mipmap-*/ic_launcher.png          遗留图标（渐变全出血 + 圆角块 + 描边云）
#   mipmap-*/ic_launcher_fg.png       自适应前景（圆角块 + 描边云，透明底）
#   mipmap-*/ic_launcher_mono.png     monochrome 专用（实心云轮廓，主题图标用）
#   mipmap-anydpi-v26/ic_launcher.xml 自适应装配（背景=@drawable/ic_launcher_bg 渐变）
#   web/public/icons + web/dist/icons PWA 三件（192/512/apple-touch，同一 512 母版）
# 云朵取自 Material Icons "cloud_outlined"（24×24 viewBox，外轮廓+内孔两条子路
# 径，evenodd 挖孔得描边效果）：内置 mini SVG 路径解析器（M/C/c/H/h/V/v/S/s/Z/z
# + 隐式重复 + 贝塞尔拉直），不引 SVG 依赖。
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
WHITE = (255, 255, 255)
TILE_ALPHA = 64              # 圆角块白透明度（≈25%，登录页 white24 同款观感）

# Material Icons "cloud_outlined"：子路径 1=外轮廓，2=内孔（evenodd 挖孔）
CLOUD_OUTLINE = ("M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 "
                 "2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 "
                 "0-2.64-2.05-4.78-4.65-4.96z"
                 "M19 18H6c-2.21 0-4-1.79-4-4s1.79-4 4-4h.71"
                 "C7.37 7.69 9.48 6 12 6c3.04 0 5.5 2.46 5.5 5.5v.5H19"
                 "c1.66 0 3 1.34 3 3s-1.34 3-3 3z")
# 实心外形（monochrome / 兜底用）：只取第一段
CLOUD_SOLID = CLOUD_OUTLINE.split("M19 18")[0]

CORNER = 90        # 遗留图标圆角半径（512 母版）
TILE_FRAC = 0.62   # 圆角块边长占画布比例（登录页品牌头同款结构）
CLOUD_IN_TILE = 0.56  # 云朵宽占圆角块边长比例（截图目测 ~55%）
TILE_RADIUS = 0.28    # 圆角块圆角占其边长比例（登录页 cloudTileRadius 20/72）
MONO_FRAC = 0.42      # monochrome 实心云占画布比例

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
    """mini SVG 路径解析：M m L l H h V v C c S s Z z + 命令隐式重复。
    返回子路径列表（每条是绝对坐标折线，闭合处补回起点）。"""
    # 数字：支持 "-4" ".5" "-.71"（SVG 常见无前导零小数——漏了会把 .5 读成 5）
    parts = re.findall(r"[MmLlHhVvCcSsZz]|-?(?:\d+(?:\.\d+)?|\.\d+)(?:e-?\d+)?", d)
    i, pos, start = 0, (0.0, 0.0), (0.0, 0.0)
    cur, subs, prev = [], [], ""
    prev_c2 = None  # 上一段三次贝塞尔的第二控制点（供 S/s 反射）

    def num():
        nonlocal i
        v = float(parts[i])
        i += 1
        return v

    def reflect():
        return (2 * pos[0] - prev_c2[0], 2 * pos[1] - prev_c2[1]) \
            if prev_c2 is not None else pos

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
        elif cmd in "Cc":
            if cmd == "C":
                p1, p2, p3 = (num(), num()), (num(), num()), (num(), num())
            else:
                p1 = (pos[0] + num(), pos[1] + num())
                p2 = (pos[0] + num(), pos[1] + num())
                p3 = (pos[0] + num(), pos[1] + num())
            cur.extend(_flatten_cubic(pos, p1, p2, p3))
            prev_c2 = p2
            pos = p3
        elif cmd in "Ss":
            p1 = reflect()  # 平滑：第一控制点=上段 c2 关于当前点镜像
            if cmd == "S":
                p2, p3 = (num(), num()), (num(), num())
            else:
                p2 = (pos[0] + num(), pos[1] + num())
                p3 = (pos[0] + num(), pos[1] + num())
            cur.extend(_flatten_cubic(pos, p1, p2, p3))
            prev_c2 = p2
            pos = p3
        elif cmd in "Zz":
            if cur:
                cur.append(start)
                subs.append(cur)
                cur = []
            pos = start
            prev_c2 = None
    if cur:
        subs.append(cur)
    return subs


def _scaled(sub, scale, ox, oy):
    return [(x * scale + ox, y * scale + oy) for (x, y) in sub]


def draw_cloud_outline(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """描边云朵画进 canvas 的 box（正方形，evenodd：外轮廓白、内孔挖空）。"""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    scale = side / 24.0
    ox = (x0 + x1) / 2 - 12 * scale
    oy = (y0 + y1) / 2 - 12 * scale  # 路径 bbox y∈[4,20]，中心即 12
    subs = parse_path(CLOUD_OUTLINE)
    mask = Image.new("L", canvas.size, 0)
    md = ImageDraw.Draw(mask)
    md.polygon(_scaled(subs[0], scale, ox, oy), fill=255)   # 外轮廓
    for hole in subs[1:]:
        md.polygon(_scaled(hole, scale, ox, oy), fill=0)    # 内孔
    white = Image.new("RGBA", canvas.size, WHITE + (255,))
    canvas.paste(white, (0, 0), mask)


def draw_cloud_solid(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """实心云朵（仅外轮廓），monochrome 用。"""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    scale = side / 24.0
    ox = (x0 + x1) / 2 - 12 * scale
    oy = (y0 + y1) / 2 - 12 * scale
    poly = _scaled(parse_path(CLOUD_SOLID)[0], scale, ox, oy)
    ImageDraw.Draw(canvas).polygon(poly, fill=WHITE)


def brand_mark(size: int, bg_image: Image.Image | None = None) -> Image.Image:
    """品牌标：渐变（或透明）底 + 半透明白圆角块 + 描边云朵。"""
    im = bg_image.convert("RGBA") if bg_image is not None \
        else Image.new("RGBA", (size, size), (0, 0, 0, 0))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tile = round(TILE_FRAC * size)
    cx = cy = size // 2
    tl, tr = cx - tile // 2, cy - tile // 2
    d.rounded_rectangle([tl, tr, tl + tile, tr + tile],
                        radius=round(TILE_RADIUS * tile),
                        fill=WHITE + (TILE_ALPHA,))
    # 圆角块内不能替换底色，须整体 alpha 合成到画布
    im = Image.alpha_composite(im, layer)
    cw = round(CLOUD_IN_TILE * tile)
    draw_cloud_outline(im, (cx - cw // 2, cy - cw // 2,
                            cx - cw // 2 + cw, cy - cw // 2 + cw))
    return im


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

    # 遗留：全出血渐变 + 圆角块描边云 + 圆角
    legacy = brand_mark(legacy_px, bg_image=gradient(legacy_px))
    rounded(legacy, round(CORNER / 512 * legacy_px)).save(os.path.join(d, "ic_launcher.png"))

    # 自适应前景：透明底 + 圆角块描边云（背景层=ic_launcher_bg 渐变）
    brand_mark(fg_px).save(os.path.join(d, "ic_launcher_fg.png"))

    # monochrome：实心云轮廓（主题图标；半透明块在单色通道里会糊）
    mono = Image.new("RGBA", (fg_px, fg_px), (0, 0, 0, 0))
    w = round(MONO_FRAC * fg_px)
    c = fg_px // 2
    draw_cloud_solid(mono, (c - w // 2, c - w // 2, c - w // 2 + w, c - w // 2 + w))
    mono.save(os.path.join(d, "ic_launcher_mono.png"))

ad = os.path.join(RES, "mipmap-anydpi-v26")
os.makedirs(ad, exist_ok=True)
with open(os.path.join(ad, "ic_launcher.xml"), "w") as f:
    f.write('<?xml version="1.0" encoding="utf-8"?>\n'
            '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@drawable/ic_launcher_bg"/>\n'
            '    <foreground android:drawable="@mipmap/ic_launcher_fg"/>\n'
            '    <monochrome android:drawable="@mipmap/ic_launcher_mono"/>\n'
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
master512 = brand_mark(512, bg_image=gradient(512))
for out_dir in PWA_DIRS:
    os.makedirs(out_dir, exist_ok=True)
    master512.save(os.path.join(out_dir, "icon-512.png"))
    master512.resize((192, 192), Image.LANCZOS).save(os.path.join(out_dir, "icon-192.png"))
    master512.resize((180, 180), Image.LANCZOS).save(os.path.join(out_dir, "apple-touch-icon.png"))

print("icon assets written:", os.path.abspath(RES), "+ PWA icons")
