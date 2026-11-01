"""服务端检查更新。

## 只查不装

自动替换正在运行的服务目录风险太大：文件被占用（Windows 会拒绝覆盖正在
执行的 exe）、升级中途断电、装了一半没有回滚路径。而安装器已经能做「覆盖
安装 + 保留数据 + 保留注册表」，把这件事交给它比自己写一个替换器可靠得多。

所以这里只回答三个问题：**现在是什么版本、最新是什么版本、有没有新的**。
新版本给下载地址，由用户跑安装器。

## 三态，不是两态

`check()` 的返回值一定会带 `error` 或 `latest`，不共用「没有新版」这张脸。
这和 App 端 `UpdateCheckResult` 是同一个道理 —— 2026-11-01 就是因为在 VPN
下两个源一起挂、`check()` 返回 null 被当成「已是最新」，用户完全无从判断。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO = "AK47ZZQ/lan-share"
TIMEOUT = 8                 # 国内访问 GitHub 经常超时，别让 UI 干等
PREFIX = "server-v"

# 版本号形如 3.77 / 3.78.1 —— 只在 tag 里取这一段
_VER_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def current_version(install_dir: Path) -> str:
    """读安装目录里的 VERSION 文件。

    老安装（3.77 及以前）没有这个文件 —— 返回空串，调用方显示「未知」
    而不是编一个数字出来。
    """
    f = Path(install_dir) / "VERSION"
    try:
        # utf-8-sig 而不是 utf-8：PS 5.1 写文件默认带 BOM，读回来第一行会变成
        # "\ufeff3.78"，正则匹配失败就显示「未知版本」。build.ps1 那边已经
        # 明确写不带 BOM，这里再兜一层，两边都不指望对方。
        text = f.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""
    first = text.splitlines()[0].strip() if text else ""
    return first if _VER_RE.fullmatch(first) else ""


def _ver_key(v: str) -> tuple[int, ...]:
    """3.9 要排在 3.10 前面，所以按段转整数比，不能按字符串比。"""
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def is_newer(latest: str, current: str) -> bool:
    if not latest:
        return False
    if not current:
        # 不知道现在是哪个版本时，不冒充「有新版本」——
        # 那会让老安装误以为一直在等更新
        return False
    try:
        return _ver_key(latest) > _ver_key(current)
    except (ValueError, TypeError):
        return False


def fetch_latest(timeout: float = TIMEOUT) -> dict:
    """拉 GitHub Releases，筛出最新的服务端包。

    返回 {"tag","version","url","notes","size","published"}。
    出错抛 RuntimeError（带人话原因），由 check() 转成 error 字段。
    """
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases?per_page=30",
        headers={
            "User-Agent": "lanshare-cli",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError("GitHub 限流了（一小时 60 次），过一会儿再试") from exc
        raise RuntimeError(f"GitHub 返回 {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"连不上 GitHub（{reason}）——检查网络或代理") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(f"连不上 GitHub（{exc}）——检查网络或代理") from exc
    except Exception as exc:
        # 兜底：调用方是 UI，任何意外异常都必须转成能显示的一句话。
        # 上面几个 except 是按经验列的，但 urllib + json 这条链上还有
        # 别的可能（编码错、SSL 上下文、代理返回的怪响应）——
        # 漏一个就会让「检查更新」按钮点下去直接崩。
        raise RuntimeError(f"检查更新失败：{exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError("GitHub 返回格式不对")

    for rel in data:
        tag = str(rel.get("tag_name") or "")
        if not tag.startswith(PREFIX):
            continue
        m = _VER_RE.search(tag)
        if not m:
            continue
        exe = None
        for a in rel.get("assets") or []:
            name = str(a.get("name") or "")
            if name.lower().endswith(".exe"):
                exe = a
                break
        if exe is None:
            # 服务端 Release 没挂 exe 说明发布时断了，跳过这条继续找
            continue
        return {
            "tag": tag,
            "version": m.group(1),
            "url": exe.get("browser_download_url", ""),
            "size": exe.get("size", 0),
            "notes": (rel.get("body") or "")[:4000],
            "published": str(rel.get("published_at") or "")[:10],
        }

    raise RuntimeError(f"GitHub 上没找到 {PREFIX}* 的 Release（是不是都删了？）")


def check(install_dir: Path, timeout: float = TIMEOUT) -> dict:
    """完整的一次检查。永远返回字典，绝不抛异常 —— 调用方是 UI。

    返回：
      {"current": "3.77", "latest": "3.78", "has_update": True,
       "url": "...", "notes": "...", "error": ""}

    error 非空表示**没查成**，此时 latest 为空、has_update 为 False。
    调用方必须把这种情况和「确实是最新版」分开显示。
    """
    cur = current_version(install_dir)
    out = {
        "current": cur,
        "latest": "",
        "has_update": False,
        "url": "",
        "notes": "",
        "published": "",
        "error": "",
    }
    try:
        info = fetch_latest(timeout)
    except RuntimeError as exc:
        out["error"] = str(exc)
        return out

    out.update(
        latest=info["version"],
        url=info["url"],
        notes=info["notes"],
        published=info["published"],
    )
    out["has_update"] = is_newer(info["version"], cur)
    return out
