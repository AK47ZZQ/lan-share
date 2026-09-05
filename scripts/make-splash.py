# -*- coding: utf-8 -*-
# 生成 flutter_native_splash 开屏资源（复用 make-app-icon.py 的品牌绘制）：
#   mobile/splash/splash_logo.png       768² 品牌标（渐变圆角块+描边云）
#   mobile/splash/splash_android12.png  1152² 透明底、中央 768 渐变圆盘
#                                       （Android 12+ windowSplashScreenAnimatedIcon
#                                        会被裁圆，透明底+居中是官方建议规格）
# v0.16.0：a12 图标从"渐变圆角方块"改"渐变圆盘"——真机上系统裁圆后方块
# 四角露色（用户实测反馈"四角没有被透明覆盖"）。盘径仍是 768（=2/3 画布，
# flutter_native_splash 安全区），观感尺寸不变，四角全透明。
# 注意：make-app-icon.py 是脚本不是库，importlib 加载时会执行一遍全量图标
# 重绘（幂等，输出与现版一致）。
import importlib.util
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

spec = importlib.util.spec_from_file_location(
    'make_app_icon', os.path.join(ROOT, 'scripts', 'make-app-icon.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

out_dir = os.path.join(ROOT, 'mobile', 'splash')
os.makedirs(out_dir, exist_ok=True)

# 与遗留桌面图标同款：渐变底 → 品牌标合成 → 圆角
logo = m.rounded(m.brand_mark(768, bg_image=m.gradient(768)), m.CORNER)
logo.save(os.path.join(out_dir, 'splash_logo.png'))


def circular(im):
    """圆形裁切（2x 超采样 + LANCZOS，边缘抗锯齿）。"""
    big = im.resize((im.width * 2, im.height * 2), m.Image.NEAREST)
    mask = m.Image.new('L', big.size, 0)
    m.ImageDraw.Draw(mask).ellipse(
        [0, 0, big.width - 1, big.height - 1], fill=255)
    mask = mask.resize(im.size, m.Image.LANCZOS)
    out = m.Image.new('RGBA', im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


a12 = m.Image.new('RGBA', (1152, 1152), (0, 0, 0, 0))
a12.alpha_composite(circular(logo), ((1152 - 768) // 2, (1152 - 768) // 2))
a12.save(os.path.join(out_dir, 'splash_android12.png'))
print('splash assets written to', out_dir)
