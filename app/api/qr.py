"""通用 QR SVG（07 批次 3B：从 system.py 按域拆出）；share.py 分享码同源复用。"""
import io

import qrcode
import qrcode.image.svg  # SvgPathImage 子模块：引用前必须显式导入
from fastapi import APIRouter, Depends, HTTPException, Query, Request
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


def qr_png_response(text: str, box_size: int = 8, border: int = 2) -> Response:
    """QR 码 PNG。给桌面端用 —— PictureBox 直接能显示，不用 SVG 渲染器。

    box_size=8 是给 300px 上下的显示区域用的，扫码距离 20cm 内清晰。
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=box_size,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/api/qr")
async def generic_qr(text: str = Query(..., min_length=1, max_length=512),
                     _user=Depends(require_user)):
    """任意文本的 QR 码 SVG（登录后可用，如手机上传页二维码）。"""
    return qr_svg_response(text)


# ──────────────────────────────────────────────────────────────
# 本机专用 QR（供桌面托盘/控制面板显示局域网地址二维码）
# ──────────────────────────────────────────────────────────────

def _effective_client_ip(request: Request) -> str:
    """已归位 app/security.py::effective_client_ip（2026-11-04 解耦）。
    保留别名：local_config 老代码与既有测试仍按本模块引用。"""
    from app.security import effective_client_ip

    return effective_client_ip(request)


@router.get("/local/qr")
async def local_qr(
    request: Request,
    text: str = Query(..., min_length=1, max_length=512),
    format: str = Query("svg", pattern="^(svg|png)$"),
):
    """本机专用的 QR 码，免登录。

    存在的理由：桌面托盘 / 控制面板没有登录态，调不动 /api/qr，
    但又需要在窗口里显示「手机扫码访问局域网地址」的二维码。

    format=png 是给桌面端准备的 —— WinForms 的 PictureBox 直接能显示 PNG，
    而 SVG 在 .NET Framework 里没有内置渲染器，为了一个二维码引入
    SVG 库不值得。

    内容限制：只是一段带进局域网地址的文本，不是秘密 ——
    真正保护数据的是那些接口自己的 require_user。
    """
    ip = _effective_client_ip(request)
    if ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail="该接口仅限本机访问",
        )
    if format == "png":
        return qr_png_response(text)
    return qr_svg_response(text)
