"""LAN Share 品牌标的唯一绘制实现 —— App、桌面、托盘、PWA 全从这里出。

为什么单独抽一个模块：之前有两套画法，App 用 SVG 路径精确渲染（描边云、
用户定版），桌面/托盘用「三个圆 + 一个矩形」近似拼（实心云、底部平切）。
结果同一个品牌在手机和电脑上长得不一样，桌面那个明显糙。

现在统一：所有图标都由这里画，只有「尺寸大小」这一个变量不同。

小尺寸的策略（这是唯一需要分叉的地方）：
    >= 48px   描边镂空云（和 App 完全一致）
    32px      描边云，云朵占比略微放大
    <= 24px   实心云 —— 16px 下描边只有 0.5 个像素，会糊成一坨白点；
              实心剪影是 Windows 小图标的通行做法，清晰度优先。

用法：
    from brandmark import brand_mark, brand_tray
    brand_mark(256).save("a.png")
"""

import re

from PIL import Image, ImageDraw, ImageFilter

# ── 品牌常量 ──
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
CLOUD_SOLID = CLOUD_OUTLINE.split("M19 18")[0]

CORNER = 90            # 遗留图标圆角半径（512 母版）
TILE_FRAC = 1.00       # 圆角块全幅覆盖
TILE_RADIUS = 0.176    # 块圆角占其边长
CLOUD_IN_TILE = 0.35   # 云朵宽占块边长比例（用户定版）
MONO_FRAC = 0.42       # monochrome 实心云占画布比例

# 小尺寸门槛：低于这个值改用实心云
_SOLID_BELOW = 32


# ── SVG 路径解析（内置，不引依赖）──
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
    """mini SVG 路径解析：M m L l H h V v C c S s Z z + 命令隐式重复。"""
    parts = re.findall(r"[MmLlHhVvCcSsZz]|-?(?:\d+(?:\.\d+)?|\.\d+)(?:e-?\d+)?", d)
    i, pos, start = 0, (0.0, 0.0), (0.0, 0.0)
    cur, subs, prev = [], [], ""
    prev_c2 = None

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
        cmd = tok if re.match(r"[A-Za-z]", tok) else prev
        if re.match(r"[A-Za-z]", tok):
            i += 1
        prev = cmd
        if cmd == "M":
            pos = (num(), num())
            start = pos
            cur = [pos]
            prev = "L"
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
            p1 = reflect()
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


# ── 绘制原语 ──
def draw_cloud_outline(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """描边云朵画进 canvas 的 box（正方形，evenodd：外轮廓白、内孔挖空）。"""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    scale = side / 24.0
    ox = (x0 + x1) / 2 - 12 * scale
    oy = (y0 + y1) / 2 - 12 * scale
    subs = parse_path(CLOUD_OUTLINE)
    mask = Image.new("L", canvas.size, 0)
    md = ImageDraw.Draw(mask)
    md.polygon(_scaled(subs[0], scale, ox, oy), fill=255)
    for hole in subs[1:]:
        md.polygon(_scaled(hole, scale, ox, oy), fill=0)
    white = Image.new("RGBA", canvas.size, WHITE + (255,))
    canvas.paste(white, (0, 0), mask)


def draw_cloud_solid(canvas: Image.Image, box: tuple[int, int, int, int],
                     alpha: int = 255) -> None:
    """实心云朵（仅外轮廓）。调用方须把 canvas 当独立透明层画好后再合成。"""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    scale = side / 24.0
    ox = (x0 + x1) / 2 - 12 * scale
    oy = (y0 + y1) / 2 - 12 * scale
    poly = _scaled(parse_path(CLOUD_SOLID)[0], scale, ox, oy)
    ImageDraw.Draw(canvas).polygon(poly, fill=WHITE + (alpha,))


def gradient(size: int) -> Image.Image:
    """斜向渐变：左上 #5B6BFF → 右下 #895BFF。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * (size - 1)) if size > 1 else 0.0
            px[x, y] = (
                int(GRAD_TOP[0] + (GRAD_BOT[0] - GRAD_TOP[0]) * t),
                int(GRAD_TOP[1] + (GRAD_BOT[1] - GRAD_TOP[1]) * t),
                int(GRAD_TOP[2] + (GRAD_BOT[2] - GRAD_TOP[2]) * t),
            )
    return img


def rounded(im: Image.Image, radius: int) -> Image.Image:
    """把方形图裁成圆角（四角透明）。"""
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, im.size[0] - 1, im.size[1] - 1], radius=radius, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


# ── 对外：品牌标 ──
def _cloud_mask_ss(size: int, ss: int = 8, frac: float = 0.74) -> Image.Image:
    """几何云 mask：在 size*ss 上画，缩回 size —— 超采样得到自然抗锯齿。

    为什么小尺寸不用 SVG 描边路径：16px 下描边只有半像素，插值后糊成一团
    白点，完全认不出是云。而纯像素网格画出来又是硬边锯齿，像 Win95。

    几何云（三个不等的圆 + 圆角底座）超采样后，既保住云的形状，边缘又有
    灰度过渡，是 16~32px 这个尺寸段的可用解。
    """
    S = size * ss
    layer = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(layer)
    w = S * frac
    cx = S / 2
    base_h = w * 0.42
    base_top = S * 0.50
    d.rounded_rectangle([cx - w / 2, base_top, cx + w / 2, base_top + base_h],
                        radius=base_h / 2, fill=255)
    # 三瓣刻意不等：左略小略低，中最高最大，右中等 —— 完全对称会显得呆板
    rl = w * 0.21
    d.ellipse([cx - w * 0.29 - rl, base_top - rl * 1.05,
               cx - w * 0.29 + rl, base_top + rl * 0.95], fill=255)
    rm = w * 0.28
    d.ellipse([cx - rm * 0.95, base_top - rm * 1.95,
               cx + rm * 1.05, base_top + rm * 0.55], fill=255)
    rr = w * 0.23
    d.ellipse([cx + w * 0.25 - rr, base_top - rr * 1.35,
               cx + w * 0.25 + rr, base_top + rr * 0.75], fill=255)
    return layer.resize((size, size), Image.LANCZOS)


def draw_cloud_ss(canvas: Image.Image, size: int, frac: float = 0.74) -> None:
    """把超采样云贴到 canvas 上（白色）。"""
    mask = _cloud_mask_ss(size, frac=frac)
    white = Image.new("RGBA", (size, size), WHITE + (255,))
    canvas.paste(white, (0, 0), mask)


def draw_cloud_ss_into(target: Image.Image, size: int, frac: float = 0.74) -> None:
    """把超采样云的 mask 画进一张 L 模式图（供后续做描边/膨胀用）。"""
    mask = _cloud_mask_ss(size, frac=frac)
    target.paste(255, (0, 0), mask)


def brand_mark(size: int, bg_image: Image.Image | None = None,
               force_outline: bool = False) -> Image.Image:
    """品牌标：渐变（或透明）底 + 圆角块 + 云朵。

    三档策略（这是唯一按尺寸分叉的地方）：
        >= 48px   描边镂空云 —— 和手机 App 完全一致（用户定版样式）
        33~47px   描边云，云朵占比放大一点
        <= 32px   超采样几何云 —— 描边在这个尺寸不足 1 像素会糊，
                  实心云 + 抗锯齿才是小尺寸的可读解
    """
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
    im = Image.alpha_composite(im, layer)

    if size <= _SOLID_BELOW and not force_outline:
        draw_cloud_ss(im, size, frac=0.72)
        return im

    cw = round(CLOUD_IN_TILE * tile)
    draw_cloud_outline(im, (cx - cw // 2, cy - cw // 2,
                            cx - cw // 2 + cw, cy - cw // 2 + cw))
    return im


def brand_icon(size: int) -> Image.Image:
    """带圆角的品牌图标（桌面/任务栏用，四角透明）。"""
    im = brand_mark(size, bg_image=gradient(size))
    return rounded(im, round(size * CORNER / 512))


def brand_tray(size: int, state_rgb: tuple[int, int, int] | None = None) -> Image.Image:
    """托盘专用图标：品牌色圆角底 + 白云（+ 可选状态点）。

    ## 为什么用彩色底而不是单色剪影

    试过单色白云方案：Windows 托盘背景随主题变，浅色主题下白云几乎看不见，
    加 1px 深色描边也救不回来 —— 16px 的抗锯齿会把那 1px 稀释成浅灰。
    彩色圆角底自带边界，深浅背景都能一眼认出，而且和桌面图标、手机 App
    是同一个视觉语言。

    ## 为什么云用超采样几何而不是描边路径

    描边在 16px 下不足 1 像素（见 brand_mark 的说明）。这里用实心几何云，
    占比放大到 0.66 —— 底和云各占一半，谁也不淹没谁。
    """
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # 底：品牌渐变，裁成小圆角（16px 下圆角约 2~3px）
    im.paste(gradient(size), (0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=max(2, round(size * 0.22)), fill=255)
    im.putalpha(mask)

    draw_cloud_ss(im, size, frac=0.66)

    if state_rgb is None:
        return im

    d = ImageDraw.Draw(im)
    # 状态点：右下角，带深色描边保证在深浅背景都跳出来
    dot = max(4, round(size * 0.30))
    x1 = y1 = size - 1
    x0, y0 = x1 - dot, y1 - dot
    d.ellipse([x0 - 1, y0 - 1, x1 + 1, y1 + 1], fill=(18, 20, 32, 240))
    d.ellipse([x0, y0, x1, y1], fill=state_rgb + (255,))
    return im
