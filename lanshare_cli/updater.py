"""检查更新：自定义更新源优先，GitHub 兜底。

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

## 自定义更新源（2026-11-04）

默认从 GitHub Releases 拉。想自己维护更新（快速迭代、不被墙、不受限流）
就在注册表里配 `UpdateUrls`，**多行、按顺序尝试、先写的优先** —— 家里把
局域网地址写在前面、樱花隧道地址写在后面，在家就不用绕一圈公网。

回退链：自定义源（逐个试）→ GitHub → 上次成功的缓存 → 明确报错。

**回退必须标注来源**：自定义源连不上却静默回落 GitHub，用户会以为「源里
没有新版」，实际是源挂了 —— 那比报错更难查。所以结果里永远带 source /
source_url / source_error 三个字段。

清单格式见 `docs/更新源协议.md`（契约，Python 和 Dart 两边照它实现）。
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
from urllib.parse import urljoin, urlsplit

REPO = "AK47ZZQ/lan-share"
TIMEOUT = 8                 # 国内访问 GitHub 经常超时，别让 UI 干等
PREFIX = "server-v"

# 自定义更新源在注册表里的落点（跟 ApiPort / DataFolder 这些放在一起）
UPDATE_URL_VALUE = "UpdateUrls"
REG_SUBKEY = "LANShare"

# 清单结构版本。将来改字段时靠它分流：老客户端看到不认识的版本要明确报错，
# 而不是硬解析出一堆 None 然后告诉用户「没有新版」。
SCHEMA = 1

# 一次最多带多少条历史，够用且不会把返回体撑大
MAX_HISTORY = 20


# ──────────────────────────────────────────────────────────────
# TLS / 代理
# ──────────────────────────────────────────────────────────────

def _ssl_context(extra_ca: Path | None = None) -> ssl.SSLContext | None:
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
    ctx: ssl.SSLContext | None = None
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None

    # 自定义更新源常常就是自己这台服务器 —— 它用的是 Caddy 内部 CA 签的自签
    # 证书。certifi 里当然没有这张 CA，于是「检查更新」会报证书校验失败，
    # 而浏览器却是正常的（系统信任库里有）。
    #
    # 这里只把**本安装自己的那张根证书**加进校验链，不做「跳过验证」：
    # 信任范围仍然是一个具体的 CA 文件，不是「什么都信」。
    if extra_ca is not None:
        try:
            if extra_ca.is_file():
                base_ctx = ctx or ssl.create_default_context()
                base_ctx.load_verify_locations(cafile=str(extra_ca))
                return base_ctx
        except Exception:
            pass        # 加载失败就退回原上下文，至少公共 CA 那条路还在
    return ctx


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


def _opener(extra_ca: Path | None = None):
    """带代理的 opener。

    优先级：环境变量 > Windows 系统代理。环境变量是显式指定的，应当尊重；
    系统代理只是「用户以为它在生效」的那一个。

    2026-11-04 修：opener 分支原先只装 ProxyHandler，TLS 上下文
    （certifi 的 CA bundle + 自定义源的自签根证书）只给了无代理的
    urlopen(context=...) 那条路 —— 开着系统代理（Clash 之类）的机器，
    自定义更新源的证书校验必挂。现在给 opener 显式装一个带上下文的
    HTTPSHandler（build_opener 会用它顶掉默认的那个）。
    """
    proxies = urllib.request.getproxies() or {}
    if not proxies.get("https") and not proxies.get("http"):
        sysproxy = _system_proxy()
        if sysproxy:
            proxies = {"http": sysproxy, "https": sysproxy}
    if not proxies:
        return None
    handlers: list = [urllib.request.ProxyHandler(proxies)]
    try:
        ctx = _ssl_context(extra_ca)
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
    except Exception:
        pass            # 上下文建不出来就退回默认 TLS 行为，代理照用
    return urllib.request.build_opener(*handlers)


def _http_json(url: str, timeout: float, *, label: str,
               extra_ca: Path | None = None) -> object:
    """GET 一个 URL 并解析 JSON。所有失败都转成带人话的 RuntimeError。

    抽出来是因为现在有两个源（自定义清单 / GitHub API）要走同一套代理、
    TLS 兜底和错误翻译。上一版这套逻辑写死在 fetch_latest 里，想加第二个
    源只能复制粘贴 —— 复制出来的错误话术迟早会走样。
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "lanshare-cli", "Accept": "application/json"},
    )
    try:
        op = _opener()
        if op is not None:
            resp_ctx = op.open(req, timeout=timeout)
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout,
                                              context=_ssl_context(extra_ca))
        with resp_ctx as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and "api.github.com" in url:
            # GitHub 会给出重置时间（UTC epoch 秒）。能读到就说人话，
            # 读不到才退回笼统的一句 —— 「过一会儿再试」用户没法安排。
            hint = ""
            try:
                reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
                if reset:
                    wait = max(0, int(reset) - int(time.time()))
                    if wait > 0:
                        mins = (wait + 59) // 60
                        hint = f"，约 {mins} 分钟后恢复"
            except Exception:
                pass
            raise RuntimeError(
                f"GitHub 接口限流（未登录每小时 60 次）{hint}。"
                "这是 GitHub 的免费额度限制，不是软件故障。") from exc
        if exc.code == 404:
            raise RuntimeError(f"{label} 返回 404 —— 地址或清单文件不存在") from exc
        raise RuntimeError(f"{label} 返回 {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # 证书错误单独说，不然用户只看到一句英文 SSL 栈
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            raise RuntimeError(
                f"连不上 {label}：TLS 证书校验失败（本机根证书不全）。"
                "如果开了 VPN 或代理，先关掉再试；仍然失败说明本地证书库缺根证书。"
            ) from exc
        raise RuntimeError(f"连不上 {label}（{reason}）——检查网络或代理") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(f"连不上 {label}（{exc}）——检查网络或代理") from exc
    except ValueError as exc:
        raise RuntimeError(f"{label} 返回的不是合法 JSON") from exc
    except Exception as exc:
        # 兜底：调用方是 UI，任何意外异常都必须转成能显示的一句话。
        # 上面几个 except 是按经验列的，但 urllib + json 这条链上还有
        # 别的可能（编码错、SSL 上下文、代理返回的怪响应）——
        # 漏一个就会让「检查更新」按钮点下去直接崩。
        raise RuntimeError(f"检查 {label} 失败：{exc}") from exc


# ──────────────────────────────────────────────────────────────
# 自定义更新源
# ──────────────────────────────────────────────────────────────

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def normalize_source(url: str) -> str:
    """校验并规范化一个更新源地址。非法抛 ValueError（带人话原因）。

    只接受 https。唯一例外是回环地址允许 http —— 在本机调试更新接口时
    不该被迫先配证书。

    为什么这么严：自定义源意味着**程序会按你给的地址去下载 exe 然后让你
    安装**。明文 http 下中间人换一个包就是远程代码执行，而用户完全看不出
    区别 —— 界面照样显示「有新版本」。
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("地址是空的")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raise ValueError(
            "地址要以 https:// 开头（比如 https://192.168.1.100:8765/updates）")

    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()

    if scheme == "https":
        return raw.rstrip("/")
    if scheme == "http":
        if host in _LOOPBACK_HOSTS:
            return raw.rstrip("/")
        raise ValueError(
            "只支持 https:// 开头。http 是明文传输，别人能在中间把安装包换掉，"
            "更新链路上不能留这个口子（127.0.0.1 / localhost 例外，供本机调试）")
    raise ValueError(f"不支持的协议 {scheme}://（只支持 https）")


def configured_sources(key: str = REG_SUBKEY) -> list[str]:
    """读注册表里的自定义更新源，按填写顺序返回（局域网地址应在前面）。

    没装、没配、读不到 —— 一律返回空列表，调用方自然走 GitHub。
    只读操作，任何异常都不该影响「检查更新」本身能不能跑。
    """
    if sys.platform != "win32":
        return []
    if not key or any(c in key for c in "\\'; "):
        return []

    raw = ""
    try:
        import winreg

        # 64 位视图优先（Inno Setup 装 64 位程序时写在 64 位视图），
        # 读不到再回落默认视图 —— 两种装机情况都见过。
        for extra in (getattr(winreg, "KEY_WOW64_64KEY", 0), 0):
            try:
                with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{key}",
                        0, winreg.KEY_READ | extra) as k:
                    raw, _ = winreg.QueryValueEx(k, UPDATE_URL_VALUE)
                if raw:
                    break
            except OSError:
                continue
    except Exception:
        return []

    out: list[str] = []
    for line in str(raw or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def set_sources(urls: list[str], key: str = REG_SUBKEY) -> None:
    """保存自定义更新源（覆盖式）。传空列表 = 清除，恢复「只走 GitHub」。

    先逐条校验再写 —— 宁可保存失败，也不能把一个 http:// 的地址存进去：
    存进去之后就变成每次检查都先往那儿发请求。

    写 HKLM 需要管理员权限。托盘是 `/rl highest` 自启的，继承下来够用；
    单独在普通命令行里跑会失败，这时把 WinError 翻译成人话抛出去。
    """
    clean: list[str] = []
    for u in urls:
        s = (u or "").strip()
        if not s:
            continue
        clean.append(normalize_source(s))

    if sys.platform != "win32":
        raise RuntimeError("只有 Windows 支持写注册表配置")
    if not key or any(c in key for c in "\\'; "):
        raise ValueError(f"非法的注册表键名: {key!r}")

    import winreg

    path = rf"SOFTWARE\{key}"
    try:
        with winreg.CreateKeyEx(
                winreg.HKEY_LOCAL_MACHINE, path, 0,
                winreg.KEY_SET_VALUE | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
            if clean:
                # 多行存着更好读：注册表编辑器里一眼能看出有几条、什么顺序
                winreg.SetValueEx(k, UPDATE_URL_VALUE, 0, winreg.REG_SZ,
                                  "\r\n".join(clean))
            else:
                try:
                    winreg.DeleteValue(k, UPDATE_URL_VALUE)
                except FileNotFoundError:
                    pass            # 本来就没配，清除算成功
    except PermissionError as exc:
        raise RuntimeError(
            "写注册表需要管理员权限。请用托盘面板保存（托盘是提权运行的），"
            "或者右键「以管理员身份运行」命令行") from exc
    except OSError as exc:
        raise RuntimeError(f"保存更新源失败：{exc}") from exc


def _manifest_url(base: str) -> str:
    """把用户填的地址换算成清单的实际 URL。

    填目录（/updates）、带尾斜杠（/updates/）、直接填文件（index.json）
    三种写法都认 —— 让人去记「到底该不该带文件名」是无谓的负担。
    """
    b = base.strip().rstrip("/")
    if b.lower().endswith(".json"):
        return b
    return b + "/index.json"


def _abs_url(u: str, base: str) -> str:
    """清单里的下载地址可以是相对路径，按清单所在目录解析。

    2026-11-04 修：base 直接填了 index.json 文件时，基准必须是它**所在的
    目录** —— 原实现拼出 `.../index.json/pkg.exe`，下载 404。契约明确
    支持填文件形式（docs/更新源协议.md 的地址表）。
    """
    if not u:
        return ""
    if re.match(r"^https?://", u, re.I):
        return u
    b = base.strip().rstrip("/")
    if b.lower().endswith(".json"):
        # 清单文件 → 所在目录；连目录都没有的裸文件名就无从解析
        b = b[: b.rfind("/")] if "/" in b else ""
    return urljoin(b + "/", u)


def _norm_item(raw: object, base: str, *, need_file: bool) -> dict:
    """把清单里的一条转成内部结构，顺便校验。

    need_file=True 用于 latest：url 和 sha256 缺一个都不行。
    历史条目只用来展示，缺字段就跳过 —— 不该因为一条历史写坏了就让整个源判死。
    """
    if not isinstance(raw, dict):
        raise RuntimeError("清单条目不是 JSON 对象")

    ver = str(raw.get("version") or "").strip()
    if not _VER_RE.fullmatch(ver):
        raise RuntimeError(f"清单里的 version 不合法：{ver!r}")

    item = {
        "tag": f"{PREFIX}{ver}",
        "version": ver,
        "build": int(raw.get("build") or 0),
        "url": _abs_url(str(raw.get("url") or "").strip(), base),
        "size": int(raw.get("size") or 0),
        "sha256": str(raw.get("sha256") or "").strip().lower(),
        "notes": str(raw.get("notes") or "")[:4000],
        "published": str(raw.get("published") or "")[:10],
    }

    if need_file:
        if not item["url"]:
            raise RuntimeError("清单缺少 latest.url（没有下载地址就等于没有这个源）")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise RuntimeError(
                "清单缺少合法的 latest.sha256。更新源必须带校验值 —— "
                "否则下载回来的包是不是你发的那个，谁也说不准")
    return item


def fetch_custom(base: str, timeout: float = TIMEOUT,
                 extra_ca: Path | None = None) -> dict:
    """从一个自定义源拉清单。返回 {"latest": {...}, "history": [...]}。

    清单不合格（缺 version / url / sha256、schema 不认识）→ 整个源作废，
    抛 RuntimeError。不做局部容错：更新链路上"宽容"等于让用户装一个来路
    不明的 exe。
    """
    url = _manifest_url(base)
    data = _http_json(url, timeout, label=f"更新源 {base}", extra_ca=extra_ca)

    if not isinstance(data, dict):
        raise RuntimeError("清单根节点不是 JSON 对象")

    schema = data.get("schema")
    if schema is not None:
        try:
            schema_i = int(schema)
        except (TypeError, ValueError):
            raise RuntimeError(f"清单 schema 字段不合法：{schema!r}") from None
        if schema_i != SCHEMA:
            raise RuntimeError(
                f"清单 schema={schema_i}，本程序只认 {SCHEMA}。"
                "这个源用的是新协议，先把程序升到支持它的版本")

    latest = _norm_item(data.get("latest"), base, need_file=True)

    history: list[dict] = []
    for raw in (data.get("history") or [])[:MAX_HISTORY]:
        try:
            history.append(_norm_item(raw, base, need_file=False))
        except RuntimeError:
            continue            # 坏一条历史不影响主流程

    return {"latest": latest, "history": history}


# ──────────────────────────────────────────────────────────────
# GitHub
# ──────────────────────────────────────────────────────────────

def fetch_github(timeout: float = TIMEOUT) -> dict:
    """拉 GitHub Releases，筛出服务端包。返回 {"latest": {...}, "history": [...]}。

    出错抛 RuntimeError（带人话原因），由 check() 转成 error 字段。
    """
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
    data = _http_json(url, timeout, label="GitHub")

    if not isinstance(data, list):
        raise RuntimeError("GitHub 返回格式不对")

    history: list[dict] = []
    latest: dict | None = None

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

        # 最新那条 Release 没挂 exe 说明发布断了，跳过它继续找下一条 ——
        # 但不能因此把整件事判死（列表里还有历史版本）。
        if exe is None:
            continue

        entry = {
            "tag": tag,
            "version": m.group(1),
            "build": 0,
            "url": exe.get("browser_download_url", ""),
            "size": exe.get("size", 0),
            "sha256": "",       # GitHub API 不给，只能靠 HTTPS 保证来源
            "notes": (rel.get("body") or "")[:4000],
            "published": str(rel.get("published_at") or "")[:10],
        }

        if latest is None:
            latest = entry
        if len(history) < MAX_HISTORY:
            history.append(entry)

    if latest is None:
        raise RuntimeError(f"GitHub 上没找到 {PREFIX}* 的 Release（是不是都删了？）")

    return {"latest": latest, "history": history}


# ── 检查结果缓存 ──────────────────────────────────────────────
#
# 为什么要缓存：GitHub 未登录 API 每小时只有 60 次。用户看到「检查失败」
# 会反复点，几次就把额度烧光，然后进入「一直失败」的恶性循环——2026-11-02
# 实机反馈正是「检查更新一直都是失败，显示被限流了」。
#
# 有了缓存：成功过一次，之后即使源不可达也能给出上次的结果，
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
        if not isinstance(d, dict):
            return None
        # 兼容 3.95 及以前的缓存（那时字段叫 version 而不是 latest）
        if not (d.get("latest") or d.get("version")):
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


def _empty_result(cur: str) -> dict:
    return {
        "current": cur,
        "latest": "",
        "has_update": False,
        "url": "",
        "size": 0,
        "sha256": "",
        "notes": "",
        "published": "",
        "history": [],
        "error": "",
        "stale": False,       # True = 这是缓存结果，不是刚查的
        "stale_hours": 0,
        # 这个结果是谁给的。UI 必须显示 —— 回落到 GitHub 却不说，
        # 用户会以为「自定义源里没有新版」。
        "source": "",
        "source_url": "",
        "source_error": "",
    }


def check(install_dir: Path, timeout: float = TIMEOUT,
          *, sources: list[str] | None = None) -> dict:
    """完整的一次检查。永远返回字典，绝不抛异常 —— 调用方是 UI。

    返回：
      {"current": "3.95", "latest": "3.96", "has_update": True,
       "url": "...", "sha256": "...", "notes": "...",
       "history": [...], "source": "custom", "source_url": "...",
       "source_error": "", "error": ""}

    error 非空表示**没查成**，此时 latest 为空、has_update 为 False。
    调用方必须把这种情况和「确实是最新版」分开显示。

    sources 传 None 表示按注册表配置；传 [] 强制只走 GitHub
    （GUI 里「恢复默认」就是这语义），传具体列表给测试用。
    """
    cur = current_version(install_dir)
    out = _empty_result(cur)

    # 本安装自己的 Caddy 根证书。自定义源很可能就是这台机器，它的证书
    # 就是这张 CA 签的 —— 不加进来的话，指向本机的更新源永远报证书错误。
    extra_ca = Path(install_dir) / "certs" / "caddy" / "pki" / "authorities" / "local" / "root.crt"

    bases = configured_sources() if sources is None else list(sources)
    failures: list[str] = []

    # ── ① 自定义源：按填写顺序逐个试，第一个能用的就用 ──
    for base in bases:
        try:
            norm = normalize_source(base)
            got = fetch_custom(norm, timeout, extra_ca=extra_ca)
        except (ValueError, RuntimeError) as exc:
            failures.append(f"{base}（{exc}）")
            continue

        item = got["latest"]
        out.update(
            latest=item["version"],
            url=item["url"],
            size=item["size"],
            sha256=item["sha256"],
            notes=item["notes"],
            published=item["published"],
            history=got["history"],
            source="custom",
            source_url=norm,
            # 前面几个源挂了也要说 —— 用户配的列表可能第 3 条才通
            source_error="；".join(failures),
        )
        out["has_update"] = is_newer(item["version"], cur)
        _write_cache(install_dir, out)
        return out

    # ── ② GitHub ──
    try:
        got = fetch_github(timeout)
        item = got["latest"]
        out.update(
            latest=item["version"],
            url=item["url"],
            size=item["size"],
            sha256=item["sha256"],
            notes=item["notes"],
            published=item["published"],
            history=got["history"],
            source="github",
            source_error="；".join(failures),
        )
        out["has_update"] = is_newer(item["version"], cur)
        _write_cache(install_dir, out)
        return out
    except RuntimeError as exc:
        failures.append(f"GitHub（{exc}）")

    # ── ③ 缓存 ──
    cached = _read_cache(install_dir)
    if cached:
        age = max(0, int(time.time()) - int(cached.get("_cached_at") or 0))
        out.update(
            latest=cached.get("latest") or cached.get("version") or "",
            url=cached.get("url", ""),
            size=cached.get("size", 0) or 0,
            sha256=cached.get("sha256", ""),
            notes=cached.get("notes", ""),
            published=cached.get("published", ""),
            history=cached.get("history", []) or [],
            stale=True,
            stale_hours=age // 3600,
            source="cache",
            source_url=cached.get("source_url", ""),
            source_error="；".join(failures),
        )
        out["has_update"] = is_newer(out["latest"], cur)
        return out

    out["error"] = "；".join(failures) or "没有任何可用的更新源"
    out["source_error"] = "；".join(failures)
    return out
