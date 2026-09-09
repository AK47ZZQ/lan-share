"""通用 QR SVG（07 批次 3B：从 system.py 按域拆出）；share.py 分享码同源复用。"""
import io

import qrcode
import qrcode.image.svg  # SvgPathImage 子模块：引用前必须显式导入
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.security import require_user

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


# QR 中心品牌云朵（v0.20 更新：实心云朵，与登录页品牌头/桌面图标同一
# 官方 cloud 闭合路径——旧版 cloud_outlined 镂空字形与品牌已不同步）
_QR_LOGO_CLOUD = ("M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 "
                  "2.5 8.36 0.27 10.89 0.27 13.77 0.27 16.66 2.5 18.89 5.39 18.89"
                  "H19.04C21.13 18.66 22.75 16.82 22.75 14.66 22.75 12.5 21.13 10.66 "
                  "19.04 10.66Z")


def qr_svg_response(text: str) -> Response:
    """QR SVG + 中心云朵 logo。ECC-Q（25% 冗余）覆盖 28% 中心 logo 可靠扫读；
    嵌套 <svg> 用百分比定位，免去解析模块数。share.py 的分享码同源复用。"""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage,
                      error_correction=qrcode.constants.ERROR_CORRECT_Q)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    logo = ('<svg x="36%" y="36%" width="28%" height="28%" viewBox="0 0 512 512">'
            '<rect width="512" height="512" rx="96" fill="#ffffff"/>'
            f'<path transform="translate(166.4,166.4) scale(7.4667)" '
            f'd="{_QR_LOGO_CLOUD}" fill="#5B6CFF"/></svg>')
    svg = svg.replace("</svg>", logo + "</svg>")
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/api/qr")
async def generic_qr(text: str = Query(..., min_length=1, max_length=512),
                     _user=Depends(require_user)):
    """任意文本的 QR 码 SVG（登录后可用，如手机上传页二维码）。"""
    return qr_svg_response(text)
