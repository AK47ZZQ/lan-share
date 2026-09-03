"""App 版本分发：版本信息 + APK 下载（自家分发闭环，App v0.6.0「检查更新」）。

约定：共享空间根下 `App安装包/version.json`：
    {"version": "0.6.0", "build": 14, "filename": "lanshare.apk",
     "sha256": "<hex 可空>", "notes": "更新说明可空"}
管理员发版时把新 APK 与 version.json 一起放进该目录即可；App「检查更新」
对比 build 号后经 /api/app/download 拉包安装。两个端点都要求登录（不出公网
探测面）；文件名一律取 basename，防 version.json 内容造成路径穿越。
"""
import base64
import hashlib
import json
import re

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.config import PROJECT_ROOT, SHARE_ROOT
from app.db import _db
from app.filetools import human_size
from app.main import app
from app.security import audit, client_ip, require_user

DIST_DIR = "App安装包"
APK_MIME = "application/vnd.android.package-archive"


def _load_version() -> dict:
    f = SHARE_ROOT / DIST_DIR / "version.json"
    if not f.is_file():
        raise HTTPException(status_code=404, detail="服务端尚未配置 App 版本信息（App安装包/version.json）")
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="version.json 解析失败")
    if not isinstance(data, dict) or not data.get("version") or "build" not in data:
        raise HTTPException(status_code=500, detail="version.json 缺少 version/build 字段")
    try:
        data["build"] = int(data["build"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=500, detail="version.json 的 build 必须是整数")
    # 只取 basename：filename 被写成 ../x 或 C:\x 时打回原形
    name = str(data.get("filename") or "lanshare.apk").replace("\\", "/").split("/")[-1]
    data["filename"] = name
    return data


def _apk_path(data: dict):
    dist = (SHARE_ROOT / DIST_DIR).resolve()
    apk = (dist / data["filename"]).resolve()
    if not str(apk).startswith(str(dist)):
        raise HTTPException(status_code=400, detail="非法的 APK 文件名")
    return apk


@app.get("/api/app/latest")
def app_latest(_user=Depends(require_user)):
    data = _load_version()
    apk = _apk_path(data)
    return {
        "version": str(data["version"]),
        "build": data["build"],
        "filename": data["filename"],
        "notes": str(data.get("notes") or ""),
        "sha256": str(data.get("sha256") or "") or None,
        "available": apk.is_file(),
        "size": apk.stat().st_size if apk.is_file() else None,
        "size_human": human_size(apk.stat().st_size) if apk.is_file() else None,
    }


def _load_history() -> list:
    """versions.json → 宽容解析（缺文件/坏条目一律跳过，App 端空列表隐藏栏目）。"""
    f = SHARE_ROOT / DIST_DIR / "versions.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("version"):
            continue
        try:
            build = int(it.get("build"))
        except (TypeError, ValueError):
            continue
        out.append({"version": str(it["version"]), "build": build,
                    "date": str(it.get("date") or ""),
                    "notes": str(it.get("notes") or "")})
    return out


@app.get("/api/app/history")
def app_history(_user=Depends(require_user)):
    """App 更新历史（App安装包/versions.json，发版时随 notes 一起维护）。"""
    return {"items": _load_history()}


@app.get("/api/app/download")
def app_download(request: Request, _user=Depends(require_user)):
    data = _load_version()
    apk = _apk_path(data)
    if not apk.is_file():
        raise HTTPException(status_code=404, detail="APK 文件不存在，请联系管理员上传")
    audit("app_download", data["filename"], ip=client_ip(request))
    return FileResponse(apk, media_type=APK_MIME, filename=apk.name)


# ─── 公网入口清单（App 登录页「主/备隧道」快捷选择）────────
# 免登录：App 在认证之前就要拿到入口列表。两个下发项均非机密——
# 公网地址本就随分享链接外发，证书指纹任何 TLS 连接者都能读到；
# 敏感的 healthz/磁盘水位仍由 is_public_entry 的 Host 匹配收敛。

_PEM_BODY = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)
_cert_fp_cache: list = []   # [指纹hex冒号格式, mtime, size]


def _cert_fingerprint() -> str:
    """本机 TLS 证书 SHA256 指纹（冒号格式与 App TOFU 弹框一致）；无证书返回空串。
    纯标准库：PEM→DER→SHA256，与 App 端 TrustStore 对同一 DER 摘要，逐字可比。"""
    path = PROJECT_ROOT / "certs" / "cert.pem"
    try:
        st = path.stat()
        if _cert_fp_cache and _cert_fp_cache[1:] == [st.st_mtime, st.st_size]:
            return _cert_fp_cache[0]
        der = base64.b64decode("".join(_PEM_BODY.findall(path.read_text(encoding="ascii"))))
        fp = ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())
    except Exception:
        return ""
    _cert_fp_cache[:] = [fp, st.st_mtime, st.st_size]
    return fp


def _setting(key: str) -> str:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


@app.get("/api/app/entries")
def app_entries():
    """主/备公网入口 + 证书指纹。地址为 null 表示未配置对应隧道；
    App 端只展示非空项，主备切换、指纹预校验都以此为准。"""
    pub = _setting("public_base_url")
    alt = _setting("public_base_url_alt")
    return {"public": pub or None, "public_alt": alt or None,
            "cert_sha256": _cert_fingerprint()}
