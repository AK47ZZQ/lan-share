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
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "AK47ZZQ/lan-share"
TIMEOUT = 8                 # 国内访问 GitHub 经常超时，别让 UI 干等
PREFIX = "server-v"


def _ssl_context() -> ssl.SSLContext | None:
    """给 urlopen 用的 TLS 上下文；拿不到可用 CA 时返回 None（用默认）。

    为什么需要这个：embedded Python 不带 CA bundle，
    `ssl.get_default_verify_paths().cafile` 是 None，只能靠 OpenSSL 去读
    Windows 证书存储。干净机器上这条路不总成立——2026-11-01 真机装 3.80，
    「检查更新」直接报：

        [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
        unable to get issuer certificate (_ssl.c:1006)

    打包 certifi 之后就有独立的 CA bundle，不再看系统证书链的脸色。
    certifi 万一没打进包（老版本残留、手工部署），退回默认上下文，
    至少还有系统那条路。
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None

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


def _system_proxy() -> str | None:
    """读 Windows 系统代理设置。

    ## 为什么必须自己读

    `urllib` 只认 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量，**完全不读 Windows
    的「Internet 选项」代理**。而绝大多数翻墙工具（Clash、v2rayN 之类）只改
    注册表、不设环境变量。

    结果就是：用户开着代理，浏览器能上 GitHub，而「检查更新」却一直报
    「连不上 GitHub」—— 报错完全指不到代理身上，用户只会觉得是软件坏了。
    2026-11-02 实机诊断里就是 `ProxyEnable=1, ProxyServer=127.0.0.1:7897`。

    返回 `http://host:port` 形式；没开或读不到返回 None。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enable:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:
        return None

    server = str(server or "").strip()
    if not server:
        return None
    # ProxyServer 可能是 "host:port"，也可能是 "http=h:p;https=h:p" 这种分协议写法
    if "=" in server:
        parts = dict(
            x.split("=", 1) for x in server.split(";") if "=" in x
        )
        server = parts.get("https") or parts.get("http") or ""
        server = server.strip()
    if not server:
        return None
    if "://" not in server:
        server = "http://" + server
    return server


def _opener():
    """带代理的 opener。

    优先级：环境变量 > Windows 系统代理。环境变量是显式指定的，应当尊重；
    系统代理只是「用户以为它在生效」的那一个。
    """
    proxies = urllib.request.getproxies() or {}
    if not proxies.get("https") and not proxies.get("http"):
        sysproxy = _system_proxy()
        if sysproxy:
            proxies = {"http": sysproxy, "https": sysproxy}
    if not proxies:
        return None
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


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
        _op = _opener()
        if _op is not None:
            resp_ctx = _op.open(req, timeout=timeout)
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout,
                                              context=_ssl_context())
        with resp_ctx as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            # GitHub 会给出重置时间（UTC epoch 秒）。能读到就说人话，
            # 读不到才退回笼统的一句 —— 「过一会儿再试」用户没法安排。
            hint = ""
            try:
                reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
                if reset:
                    import time as _t
                    wait = max(0, int(reset) - int(_t.time()))
                    if wait > 0:
                        mins = (wait + 59) // 60
                        hint = f"，约 {mins} 分钟后恢复"
            except Exception:
                pass
            raise RuntimeError(
                f"GitHub 接口限流（未登录每小时 60 次）{hint}。"
                "这是 GitHub 的免费额度限制，不是软件故障。") from exc
        raise RuntimeError(f"GitHub 返回 {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # 证书错误单独说，不然用户只看到一句英文 SSL 栈
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            raise RuntimeError(
                "连不上 GitHub：TLS 证书校验失败（本机根证书不全）。"
                "如果开了 VPN 或代理，先关掉再试；仍然失败说明本地证书库缺根证书。"
            ) from exc
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


# ── 检查结果缓存 ──────────────────────────────────────────────
#
# 为什么要缓存：GitHub 未登录 API 每小时只有 60 次。用户看到「检查失败」
# 会反复点，几次就把额度烧光，然后进入「一直失败」的恶性循环——2026-11-02
# 实机反馈正是「检查更新一直都是失败，显示被限流了」。
#
# 有了缓存：成功过一次，之后即使 GitHub 不可达也能给出上次的结果，
# 并在 UI 上标注「这是几小时前的数据」，而不是干脆报错。
#
# 注意这里是**可信上限**，不是刷新间隔：每次 check() 都会先尝试真的去查，
# 查失败才退回缓存。超过这个时限的缓存不再采用 —— 一年前的「最新版」
# 报给用户比报错更误导。
_CACHE_MAX_AGE = 7 * 86400


def _cache_file(install_dir: Path) -> Path:
    return Path(install_dir) / "cache" / "update-check.json"


def _read_cache(install_dir: Path) -> dict | None:
    """读缓存；超过 _CACHE_MAX_AGE 视为没有。"""
    try:
        f = _cache_file(install_dir)
        if not f.is_file():
            return None
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not d.get("version"):
            return None
        age = time.time() - float(d.get("_cached_at") or 0)
        if age > _CACHE_MAX_AGE:
            return None          # 太旧了，不如老实说「没查成」
        return d
    except Exception:
        return None


def _write_cache(install_dir: Path, info: dict) -> None:
    try:
        f = _cache_file(install_dir)
        f.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(info)
        payload["_cached_at"] = int(time.time())
        f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass   # 缓存写不进去不影响主流程


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
        "stale": False,      # True = 这是缓存结果，不是刚查的
        "stale_hours": 0,
    }
    try:
        info = fetch_latest(timeout)
        _write_cache(install_dir, info)
    except RuntimeError as exc:
        # 查不成时退回缓存：有旧结果就给旧结果 + 标注新鲜度，
        # 让用户至少知道「上次看到的最新版是什么」。
        cached = _read_cache(install_dir)
        if cached:
            age = max(0, int(time.time()) - int(cached.get("_cached_at") or 0))
            hours = age // 3600
            out.update(
                latest=cached.get("version", ""),
                url=cached.get("url", ""),
                notes=cached.get("notes", ""),
                published=cached.get("published", ""),
                stale=True,
                stale_hours=hours,
            )
            out["has_update"] = is_newer(out["latest"], cur)
            return out
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
