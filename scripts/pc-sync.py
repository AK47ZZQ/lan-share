"""LAN Share PC 照片备份脚本（零第三方依赖，标准库直连 WebDAV）。

用途：把本机照片目录（默认 Windows 图片库）增量同步到 LAN Share 的
WebDAV 端点 /dav/，落位到 照片/PC备份/<本机名>/ 下；已备份过（同路径+
同大小+同 mtime）的文件自动跳过，支持反复跑、断点天然续传。

前置（一次性）：
  1. 在网页版 账号设置 → WebDAV 独立密码 里设置独立密码（别用登录密码跑脚本）。
  2. 修改下方 CONFIG 四行（服务器地址/账号/WebDAV 密码/本机照片目录）。
     密码也可以放环境变量 LANSHARE_DAV_PASS，避免写进文件。

用法：
  python pc-sync.py            # 全量增量同步一次（可放进任务计划每天跑）
  python pc-sync.py --dry-run  # 只看会传什么，不真正上传

任务计划（每天 12:00 与 23:00 各跑一次，示例）：
  schtasks /Create /TN "lan-share-pc-sync" /SC DAILY /ST 12:00
    /TR "python C:\\lan-share\\scripts\\pc-sync.py"
"""
import os
import argparse
import base64
import datetime
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ── CONFIG：按需修改 ──────────────────────────────────────
SERVER = os.environ.get("LANSHARE_SERVER", "https://192.168.1.100:8765")  # 用 LANSHARE_SERVER 指定，默认示例地址
USERNAME = os.environ.get("USERNAME", "pc-user")   # LAN Share 账号名
DAV_PASS = os.environ.get("LANSHARE_DAV_PASS", "")  # WebDAV 独立密码
SOURCE = Path(os.path.expanduser("~/Pictures"))     # 要备份的本机目录
REMOTE_DIR = f"照片/PC备份/{os.environ.get('COMPUTERNAME', 'PC')}/"
EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".mp4", ".mov"}
MAX_SINGLE = 20 * 1024 * 1024 * 1024                # WebDAV 上限 20GB，保险起见声明在此
# ─────────────────────────────────────────────────────────

DAV_BASE = SERVER.rstrip("/") + "/dav/"
AUTH = "Basic " + base64.b64encode(f"{USERNAME}:{DAV_PASS}".encode()).decode()
CTX = None  # 自签证书：不校验（TOFU 形态）；如需严格校验自行换 httpx+pinning


def _opener():
    import ssl
    global CTX
    if CTX is None:
        CTX = ssl.create_default_context()
        CTX.check_hostname = False
        CTX.verify_mode = ssl.CERT_NONE     # 自签证书（内网家庭服务约定俗成）
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))


def dav_request(method: str, path: str, data: bytes | None = None,
                headers: dict | None = None) -> tuple[int, bytes]:
    url = DAV_BASE + urllib.request.quote(path)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", AUTH)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _opener().open(req, timeout=60) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)


def dav_list(remote_dir: str) -> dict[str, tuple[int, float]]:
    """PROPFIND depth-infinity 列目录：{相对路径: (size, mtime)}。"""
    body = ("<?xml version='1.0'?><d:propfind xmlns:d='DAV:'>"
            "<d:prop><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>").encode()
    status, data = dav_request("PROPFIND", remote_dir, data=body,
                               headers={"Depth": "infinity", "Content-Type": "application/xml"})
    out: dict[str, tuple[int, float]] = {}
    if status == 404:
        return out   # 目录还没建
    if status != 207:
        raise RuntimeError(f"PROPFIND 失败 HTTP {status}（检查地址/账号/WebDAV 密码）")
    ns = {"d": "DAV:"}
    root = ET.fromstring(data)
    base = urllib.request.unquote(remote_dir).strip("/")
    for resp in root.findall("d:response", ns):
        href = urllib.request.unquote(resp.findtext("d:href", "", ns))
        rel = href.strip("/")[len(base):].lstrip("/") if href.strip("/") != base else ""
        if not rel:
            continue
        size = int(resp.findtext(".//d:getcontentlength", "0", ns) or 0)
        lm = resp.findtext(".//d:getlastmodified", "", ns)
        mtime = 0.0
        if lm:
            try:
                mtime = datetime.datetime.strptime(
                    lm, "%a, %d %b %Y %H:%M:%S %Z").replace(
                    tzinfo=datetime.timezone.utc).timestamp()
            except ValueError:
                pass
        out[rel] = (size, mtime)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DAV_PASS:
        print("未配置 WebDAV 密码：设置环境变量 LANSHARE_DAV_PASS，或改脚本 CONFIG", file=sys.stderr)
        return 2
    if not SOURCE.is_dir():
        print(f"源目录不存在：{SOURCE}", file=sys.stderr)
        return 2

    existing = dav_list(REMOTE_DIR)
    uploaded = skipped = failed = 0
    for f in sorted(SOURCE.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in EXTS or f.name.startswith("."):
            continue
        rel = f.relative_to(SOURCE).as_posix()
        st = f.stat()
        old = existing.get(rel)
        if old and old[0] == st.st_size and abs(old[1] - st.st_mtime) < 2:
            skipped += 1
            continue
        if args.dry_run:
            print(f"[dry] PUT {rel} ({st.st_size} bytes)")
            uploaded += 1
            continue
        status, _ = dav_request(
            "PUT", REMOTE_DIR + rel, data=f.read_bytes(),
            headers={"Content-Type": "application/octet-stream"})
        if status in (200, 201, 204):
            uploaded += 1
            print(f"✓ {rel}")
        else:
            failed += 1
            print(f"✗ HTTP {status}: {rel}", file=sys.stderr)
    print(f"\n完成：上传 {uploaded}，跳过 {skipped}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
