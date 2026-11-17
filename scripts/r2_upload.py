"""把 LANShare 更新包与清单推到 Cloudflare R2（公网更新源）。

背景（2026-11-17）：实测同一条家宽，从 Cloudflare 边缘拉 11.6 MB/s，
从 GitHub Release 拉只有 35 KB/s（103MB 的 APK：9 秒 vs 50 分钟以上）。
R2 因此作为「第四条通道」——不替代局域网 API 与 GitHub，只补上
「人在外面、需要快速拿到包」这一段。

用法
----
    python scripts/r2_upload.py --line app --limit 3
    python scripts/r2_upload.py --line server --limit 3
    python scripts/r2_upload.py --line app --dry-run     # 只看要传什么

凭据
----
`%HERMES_HOME%/.env`（或环境变量）里的三件套：

    R2_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
    R2_ACCESS_KEY_ID=...
    R2_SECRET_ACCESS_KEY=...

**必须是 R2 的 S3 token（Object Read & Write，限定 bucket），不是 CF API Token。**

为什么走 S3 而不是 CF REST 的 `PUT /objects`
------------------------------------------
R2 对象本身单次可传 5 GiB，但 Cloudflare 各档计划的**请求体上限是 100 MB**
（Free/Pro 都是），而 App 的 APK 现在是 102 MiB —— 正好卡在线上。S3 端点
不经过那个网关，没有这个问题。

约定
----
· 清单里的 `url` 是**相对文件名**，包必须与清单同目录（与服务端契约一致）。
· `index.json` 设 `no-cache`：设错了用户永远看到旧版本，是本项目踩过的坑。
· 包文件名带版本号、内容不可变 → `immutable` 长缓存。
· 只上传本地确实存在的产物；清单里对不上文件的条目清掉 url/size/sha256，
  宁可留空也不编一个 404 的地址（沿用 release.py 的 `need_file=False` 约定）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

try:                        # 可选依赖：只有发版上传时才需要
    import boto3
except ImportError:         # pragma: no cover - 生产运行时不需要它
    boto3 = None

BUCKET = "lanshare-updates"
PUBLIC_BASE = "https://dl.ak47zzq.com"

# 清单签名的覆盖范围（契约，App 与 CLI 两侧必须逐字一致）：
#   v1|<version>|<build>|<url>|<sha256>|<size>
# 为什么不签整份 JSON：Python 与 Dart 的序列化在键序/浮点/转义上并不一致，
# 重序列化必然对不上；字段拼接既稳定又刚好覆盖了所有关键点
# （版本、包地址、包哈希、大小），history 不参与自动更新，不签。
SIG_PREFIX = "v1"


def _sig_payload(latest: dict) -> bytes:
    return "|".join([
        SIG_PREFIX,
        str(latest.get("version", "")),
        str(latest.get("build", "")),
        str(latest.get("url", "")),
        str(latest.get("sha256", "")),
        str(latest.get("size", "")),
    ]).encode("utf-8")


def sign_manifest(manifest: dict) -> dict:
    """给清单盖上 Ed25519 签名（原地修改并返回）。

    私钥来自 %HERMES_HOME%/.env 的 UPDATE_SIGNING_KEY，**绝不进仓库**。
    没有私钥时直接报错而不是「跳过签名」—— 静默跳过会让客户端在
    「验签变可选」的假象下继续工作，等于没做。
    """
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    e = _env()
    raw = e.get("UPDATE_SIGNING_KEY", "")
    if not raw:
        raise SystemExit(
            "缺少 UPDATE_SIGNING_KEY（Ed25519 私钥，base64 raw）。"
            "它在 %HERMES_HOME%/.env 里；丢了就只能换公钥并强制所有人升级。"
        )
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    manifest.pop("signature", None)
    manifest["signature"] = base64.b64encode(
        key.sign(_sig_payload(manifest.get("latest") or {}))
    ).decode("ascii")
    return manifest

# 生产安装目录（安装器布局）——清单与包的真实落点
INSTALL_DIR = Path(r"C:/lanshare")


def log(msg: str) -> None:
    print(msg, flush=True)


def _env() -> dict[str, str]:
    """环境变量优先，其次 Hermes 的 .env（凭据只放那儿，不进仓库）。"""
    out: dict[str, str] = {}
    for cand in (
        Path(os.environ.get("HERMES_HOME", "")) / ".env",
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
    ):
        if not cand.is_file():
            continue
        for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            k = k.strip()
            if sep and k and not k.startswith("#"):
                out.setdefault(k, v.strip())
    # 只让**非空**的环境变量覆盖文件里的值：某些环境会占位导出空的 R2_*，
    # 那会把 .env 里真实凭据盖成空串（2026-11-17 实测踩到）。
    out.update({
        k: v for k, v in os.environ.items()
        if k.startswith("R2_") and v.strip()
    })
    return out


def s3_client():
    """R2 的 S3 客户端（懒加载 boto3，避免给生产运行时加依赖）。"""
    if boto3 is None:
        raise SystemExit("上传需要 boto3：pip install boto3")
    e = _env()
    missing = [
        k for k in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
        if not e.get(k)
    ]
    if missing:
        raise SystemExit(
            f"缺少 {', '.join(missing)}。在 %HERMES_HOME%/.env 填好 R2 三件套："
            "R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY"
            "（R2 > Manage R2 API Tokens > Object Read & Write，限定 bucket）"
        )
    return boto3.client(
        "s3",
        endpoint_url=e["R2_ENDPOINT"],
        aws_access_key_id=e["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def manifest_dir(line: str) -> Path:
    return INSTALL_DIR / "updates" if line == "server" else INSTALL_DIR / "updates" / "app"


def load_manifest(line: str) -> dict:
    p = manifest_dir(line) / "index.json"
    if not p.is_file():
        raise SystemExit(f"清单不存在：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def trim(manifest: dict, keep: int, line: str) -> dict:
    """只留 latest + 最近 keep 条「本地真有文件」的历史。

    对不上文件的条目**保留但抹掉 url/size/sha256** —— 客户端仍能看到
    「有过这个版本」，点击下载会明确告知不可用，而不是给一个 404 地址。
    """
    dest = manifest_dir(line)
    present = {
        f.name for f in dest.iterdir()
        if f.is_file() and f.suffix.lower() in (".exe", ".apk")
    } if dest.is_dir() else set()

    kept = 0
    hist_out = []
    for h in manifest.get("history", []):
        name = h.get("url") or ""
        has_file = bool(name) and name in present
        if has_file and kept < keep:
            kept += 1
            hist_out.append(h)
        else:
            item = {k: v for k, v in h.items() if k not in ("url", "size", "sha256")}
            hist_out.append(item)

    out = dict(manifest)
    out["history"] = hist_out
    lat = dict(out.get("latest") or {})
    if not lat.get("url") or lat["url"] not in present:
        raise SystemExit(
            f"latest 指向的 {lat.get('url')!r} 不在 {dest} 里，先跑一次发版脚本"
        )
    out["latest"] = lat
    return out


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_size(client, key: str) -> int | None:
    """远端对象大小；不存在返回 None。"""
    try:
        return int(client.head_object(Bucket=BUCKET, Key=key)["ContentLength"])
    except Exception:
        return None


def r2_put(client, key: str, data: bytes | Path, content_type: str, cache: str,
           *, force: bool = False) -> int:
    extra = {"ContentType": content_type, "CacheControl": cache}
    if isinstance(data, Path):
        size = data.stat().st_size
        # 文件名带版本号 ⇒ 内容不该变。已存在且大小一致就跳过，
        # 否则每次 `--limit 3` 都会把 100MB 级的包再传一遍（实测 33 秒/个）。
        # 同名内容真变了（重打包）用 --force 覆盖。
        if not force and _remote_size(client, key) == size:
            log(f"· {key}  已存在且大小一致，跳过")
            return size
        with data.open("rb") as f:
            client.upload_fileobj(f, BUCKET, key, ExtraArgs=extra)
        return size
    client.put_object(Bucket=BUCKET, Key=key, Body=data, **extra)
    return len(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", choices=["app", "server"], required=True)
    ap.add_argument("--limit", type=int, default=3, help="保留最近几个带文件的版本")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="已存在且大小一致的对象也重传（重打包了同名文件时用）")
    args = ap.parse_args()

    raw = load_manifest(args.line)
    trimmed = trim(raw, args.limit, args.line)

    dest = manifest_dir(args.line)
    lat = trimmed["latest"]
    apk = dest / lat["url"]
    size_mb = apk.stat().st_size / 1024 / 1024

    log(f"线：{args.line}   目录：{dest}")
    log(f"latest：{lat['version']} (build {lat.get('build')})  {lat['url']}  {size_mb:.1f} MB")
    log(f"history：{len(trimmed['history'])} 条（带文件的 {args.limit} 条封顶）")
    for h in trimmed["history"][: args.limit + 2]:
        mark = "有包" if h.get("url") else "  无包"
        log(f"  · {h.get('version')} build={h.get('build')} {mark}")

    if args.dry_run:
        log("\n--dry-run：没有上传任何东西")
        return 0

    client = s3_client()
    prefix = "app" if args.line == "app" else "server"

    # 1) 清单先传（no-cache），让指向立刻正确
    sign_manifest(trimmed)
    body = json.dumps(trimmed, ensure_ascii=False, indent=2).encode("utf-8")
    n = r2_put(client, f"{prefix}/index.json", body, "application/json; charset=utf-8",
               "no-cache")
    log(f"\n✓ index.json  {n} 字节  (Cache-Control: no-cache)")

    # 2) 包后传（immutable）；清单先于包会短暂 404，但下载方拿到清单必然要等，
    #    顺序反过来则更糟：用户可能拉到包但清单里还没有它。
    uploaded = 0
    for h in trimmed["history"]:
        name = h.get("url")
        if not name:
            continue
        f = dest / name
        if not f.is_file():
            continue
        real = sha256_of(f)
        if h.get("sha256") and h["sha256"] != real:
            raise SystemExit(f"{name} 的 sha256 与清单不符（本地包被改过？）")
        got = r2_put(client, f"{prefix}/{name}", f, "application/octet-stream",
                     "public, max-age=31536000, immutable", force=args.force)
        uploaded += 1
        log(f"✓ {name}  {got / 1024 / 1024:.1f} MB")

    # 3) latest 若不在 history 里（老清单常见），单独补一次
    if not any(h.get("url") == lat["url"] for h in trimmed["history"]):
        got = r2_put(client, f"{prefix}/{lat['url']}", apk, "application/octet-stream",
                     "public, max-age=31536000, immutable", force=args.force)
        uploaded += 1
        log(f"✓ {lat['url']}  {got / 1024 / 1024:.1f} MB")

    log(f"\n完成：{uploaded} 个包 + 1 份清单 → {PUBLIC_BASE}/{prefix}/")
    log(f"验证：curl -s {PUBLIC_BASE}/{prefix}/index.json | head -c 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
