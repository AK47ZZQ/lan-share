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
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.config import PROJECT_ROOT, SHARE_ROOT
from app.filetools import human_size
from app.security import audit, client_ip, require_user
from app.settings import PUBLIC_BASE_URL, PUBLIC_BASE_URL_ALT, get_setting

DIST_DIR = "App安装包"
APK_MIME = "application/vnd.android.package-archive"

# ─────────────────────────────────────────────────────────────
# GitHub 作为 App 更新的主源（2026-11-01）
#
# 为什么换：服务端原来只读本地 `App安装包/version.json`，那是**手动维护**的
# —— 发版时忘了同步，App 就永远推不出新版（真发生过：GitHub 上 1.1.18 已经
# 发了，version.json 还停在 1.1.17/124，手机点检查更新毫无反应）。
#
# GitHub Release 是**发布动作本身**，不存在「忘了同步」；而且 Release 的
# body 天然就是更新日志，一次发布把版本号、APK、说明全带上了。
#
# 本地 version.json 保留为**兜底**：GitHub 不可达（公司网络、限流、断网）时
# 仍然能推出版本。两条路都不通才报错。
#
# 限流：未认证的 GitHub API 是 60 次/小时。检查和下载都走这里，不缓存
# 会被打爆 —— 5 分钟 TTL + 单飞锁，正常使用一小时最多 12 次。
# ─────────────────────────────────────────────────────────────
GITHUB_RELEASE_API = "https://api.github.com/repos/AK47ZZQ/lan-share/releases"
_GH_TTL = 300.0        # 缓存 5 分钟
_GH_TIMEOUT = 4.0      # 短超时：这是可选源，卡住不如放弃
_gh_cache: dict = {"ts": 0.0, "items": []}
_gh_lock = threading.Lock()


def _parse_version(text: str) -> tuple[str, int] | None:
    """从 tag/文件名里取 (version, build)。

    build 号三级兜底，优先级从高到低：
      1. 文件名里的 `+N`（`lanshare-v1.1.16+123.apk`）—— 最准，是发布号
      2. tag 里的 `+N`
      3. 用 semver 推算 `major*10000 + minor*100 + patch` —— 保证单调可比，
         虽然和真实的 flutter versionCode 不同，但只用于「谁更新」的比较，
         不参与任何安装校验

    第 3 条是必需的：早期 Release 的资产叫 `LANShare-App-1.1.18.apk`，没有 `+N`，
    App 端解析不到 build 就直接放弃这个源 —— 这是「检查更新没反应」的
    第二个原因（第一个是拿错了 Release）。
    """
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    version = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    # `+N` 是主流格式；早期还有 `_N`（lanshare-v1.1.15_122.apk）
    bm = re.search(r"[+_](\d+)", text or "")
    if bm:
        return version, int(bm.group(1))
    return version, int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))


def _github_app_releases(force: bool = False) -> list:
    """GitHub 上 tag 以 `app-` 开头的 Release，按 build 降序。

    失败一律返回上次缓存（或空列表），**绝不抛异常** —— 这是可选源，
    它挂了不能连累本地兜底那条路。
    """
    now = time.monotonic()
    with _gh_lock:
        if not force and _gh_cache["items"] and now - _gh_cache["ts"] < _GH_TTL:
            return _gh_cache["items"]

    items: list = []
    try:
        req = urllib.request.Request(
            f"{GITHUB_RELEASE_API}?per_page=30",
            headers={
                "Accept": "application/vnd.github+json",
                # GitHub API 强制要求 UA，缺了返回 403
                "User-Agent": "LANShare-Server",
            },
        )
        with urllib.request.urlopen(req, timeout=_GH_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        for rel in raw if isinstance(raw, list) else []:
            if not isinstance(rel, dict):
                continue
            tag = str(rel.get("tag_name") or "")
            if not tag.startswith("app-"):
                continue
            assets = rel.get("assets") or []
            apk = next(
                (a for a in assets
                 if isinstance(a, dict)
                 and str(a.get("name") or "").lower().endswith(".apk")),
                None,
            )
            if not apk:
                continue
            parsed = _parse_version(str(apk.get("name") or "")) or _parse_version(tag)
            if not parsed:
                continue
            version, build = parsed
            digest = str(apk.get("digest") or "")
            items.append({
                "version": version,
                "build": build,
                "filename": str(apk.get("name") or "lanshare.apk"),
                "notes": str(rel.get("body") or ""),
                "sha256": digest[len("sha256:"):].strip() if digest.startswith("sha256:") else "",
                "size": apk.get("size"),
                "date": str(rel.get("published_at") or "")[:10],
                "download_url": str(apk.get("browser_download_url") or ""),
                "html_url": str(rel.get("html_url") or ""),
            })
    except Exception:
        with _gh_lock:
            return _gh_cache["items"]

    # 排序必须按 version 元组，不能按 build。
    #
    # 两种编号体系混在一起：`+126` 是真实 versionCode，而早期 Release
    # （LANShare-App-1.1.18.apk）没有 build 号，只能退化成
    # `major*10000+minor*100+patch` = 10118 —— 量级差了两个数量级，
    # 一按 build 排就会得出「1.1.15 比 1.1.17 新」这种鬼结果。
    # version 字符串才是版本先后的唯一真相，build 只用于同版本内区分。
    items.sort(
        key=lambda x: tuple(int(n) for n in x["version"].split(".")),
        reverse=True,
    )
    if items:
        with _gh_lock:
            _gh_cache["ts"] = now
            _gh_cache["items"] = items
    return items



def _load_version_local() -> dict:
    """本地 `App安装包/version.json`（GitHub 不可达时的兜底）。"""
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
    data["_source"] = "local"
    return data


def _load_version() -> dict:
    """版本信息：**GitHub 主源 → 本地 version.json 兜底**。

    返回值多带一个 `_source` 键（"github" / "local"）：
    `/api/app/download` 靠它决定是 302 去 GitHub 还是读本地文件。
    """
    gh = _github_app_releases()
    if gh:
        data = dict(gh[0])
        data["_source"] = "github"
        return data
    return _load_version_local()


def _apk_path(data: dict):
    dist = (SHARE_ROOT / DIST_DIR).resolve()
    apk = (dist / data["filename"]).resolve()
    try:
        apk.relative_to(dist)   # 带路径分隔符的前缀判定（R2-P3-9，防同前缀目录绕过）
    except ValueError:
        raise HTTPException(status_code=400, detail="非法的 APK 文件名")
    return apk


_apk_sha_cache: list = ["", None, None]   # [sha256hex, mtime, size]
_apk_sha_lock = threading.Lock()          # 06-P2：并发读写无锁（App 后台高频检查更新）


def _apk_sha256(apk: Path) -> str:
    """APK sha256 按 mtime+size 缓存（9.9 复审 P2）：App 后台高频检查更新，
    不能每次都全量读 85MB 重算——只在发版替换文件后重算一次。
    加锁保护缓存读写（06-P2）：结果幂等，锁主要防并发下的重复全量计算。"""
    st = apk.stat()
    with _apk_sha_lock:
        if _apk_sha_cache[1:] == [st.st_mtime, st.st_size]:
            return _apk_sha_cache[0]
        h = hashlib.sha256()
        with open(apk, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        _apk_sha_cache[:] = [h.hexdigest(), st.st_mtime, st.st_size]
        return _apk_sha_cache[0]


# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.get("/api/app/latest")
def app_latest(_user=Depends(require_user)):
    data = _load_version()

    # GitHub 源：信息全在 Release 里，不用碰本地文件
    if data.get("_source") == "github":
        size = data.get("size")
        return {
            "version": str(data["version"]),
            "build": data["build"],
            "filename": data["filename"],
            "notes": str(data.get("notes") or ""),
            "sha256": data.get("sha256") or None,
            "available": bool(data.get("download_url")),
            "size": size,
            "size_human": human_size(size) if size else None,
            "source": "github",
            "html_url": data.get("html_url") or "",
        }

    apk = _apk_path(data)
    # 服务端自算 sha256（红队 P2）：version.json 手填值可能缺失/过期——
    # 客户端已改为 sha256 缺失即拒装（fail closed），服务端兜底自算保证链路可用。
    # APK ~85MB，md5 级耗时只在检查更新时发生一次
    sha = str(data.get("sha256") or "")
    if not sha and apk.is_file():
        sha = _apk_sha256(apk)
    return {
        "version": str(data["version"]),
        "build": data["build"],
        "filename": data["filename"],
        "notes": str(data.get("notes") or ""),
        "sha256": sha or None,
        "available": apk.is_file(),
        "size": apk.stat().st_size if apk.is_file() else None,
        "size_human": human_size(apk.stat().st_size) if apk.is_file() else None,
        "source": "local",
    }


def _load_history() -> list:
    """更新历史：**GitHub Release 列表优先**，本地 versions.json 兜底。

    GitHub 的每个 Release body 就是更新日志 —— 发版时写的那段说明，
    不用再单独维护一份 versions.json。这正是「更新日志也从 GitHub 拉」
    的实现方式。
    """
    gh = _github_app_releases()
    if gh:
        return [
            {"version": it["version"], "build": it["build"],
             "date": it.get("date") or "", "notes": it.get("notes") or "",
             "html_url": it.get("html_url") or ""}
            for it in gh
        ]
    return _load_history_local()


def _load_history_local() -> list:
    """本地 versions.json → 宽容解析（缺文件/坏条目一律跳过）。"""
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
        raw_build = it.get("build")
        if raw_build is None:
            continue
        try:
            build = int(raw_build)
        except (TypeError, ValueError):
            continue
        out.append({"version": str(it["version"]), "build": build,
                    "date": str(it.get("date") or ""),
                    "notes": str(it.get("notes") or "")})
    return out


@router.get("/api/app/history")
def app_history(_user=Depends(require_user)):
    """App 更新历史（App安装包/versions.json，发版时随 notes 一起维护）。"""
    return {"items": _load_history()}


@router.get("/api/app/download")
def app_download(request: Request, _user=Depends(require_user)):
    data = _load_version()

    # GitHub 源：直接 302 到 Release 资产的下载地址。
    #
    # 为什么不中转：APK 100MB，中转等于自家服务器出带宽还占连接，
    # 而 GitHub 的 CDN 快得多。而且 302 到 objects.githubusercontent.com
    # 是浏览器的标准行为，Android 的下载器一样吃。
    #
    # 保留 302 而不是 301：版本会变，永久重定向会被客户端缓存住。
    if data.get("_source") == "github":
        target = data.get("download_url") or ""
        if not target.startswith("https://"):
            raise HTTPException(status_code=502, detail="GitHub 未提供可用的下载地址")
        audit("app_download", f"github:{data['filename']}", ip=client_ip(request))
        return RedirectResponse(target, status_code=302)

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


_ENTRIES_TTL = 60.0   # 秒；App 每 20s/端探测一次，60s 缓存把 N 客户端 × 3 设施
_ENTRIES_CACHE: list = []   # [(monotonic_ts, resp_dict)]，set_public_url 时清（先例 reset_netloc_cache）


@router.get("/api/app/entries")
def app_entries():
    """主/备公网入口 + 本机局域网地址 + 证书指纹。地址为 null 表示未配置对应隧道；
    App 端只展示非空项，主备切换、指纹预校验都以此为准。"""
    now = time.monotonic()
    if _ENTRIES_CACHE and now - _ENTRIES_CACHE[0][0] < _ENTRIES_TTL:
        return _ENTRIES_CACHE[0][1]
    resp = {"public": get_setting(PUBLIC_BASE_URL) or None,
            "public_alt": get_setting(PUBLIC_BASE_URL_ALT) or None,
            "lan": _lan_addresses(),
            "cert_sha256": _cert_fingerprint()}
    _ENTRIES_CACHE[:] = [(now, resp)]
    return resp


def reset_entries_cache() -> None:
    """管理端改公网地址后即时生效（对抗审查 P2-2；先例 security.reset_netloc_cache）。"""
    _ENTRIES_CACHE.clear()


_LAN_PORT = 8765   # 局域网入口端口（Caddy TLS），与 Caddyfile 同源


def _lan_addresses() -> list[str]:
    """本机局域网 IPv4 → https://<ip>:8765 候选（反馈 20260924220138-b8b3）。

    App 的 LAN 候选原本只来自手动添加的地址历史；服务器 DHCP 换址后旧 IP
    永久失联。经本端点向 App 广播现值，App 在历史候选全失败时自动再发现。
    安全边界：本端点无认证，内网地址对任何能到达本端点者可见——但均为
    RFC1918 私网地址（公网不可路由），泄漏价值仅限拓扑形态，有意接受；
    该字段不能被用于把 App 导向假服务器：App 端探测走 TrustStore TLS
    握手裁决 + 响应指纹同源复核双门槛，伪造 lan 字段最坏是指向错误地址
    的 DoS，不构成凭据风险。"""
    out: list[str] = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return out
    for info in infos:
        ip = str(info[4][0])
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            continue
        o = [int(p) for p in parts]
        is_private = (o[0] == 10 or (o[0] == 172 and 16 <= o[1] <= 31)
                      or (o[0] == 192 and o[1] == 168))
        if is_private:
            addr = f"https://{ip}:{_LAN_PORT}"
            if addr not in out:
                out.append(addr)
    return out[:4]
