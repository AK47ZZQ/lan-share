"""`/api/app/entries` 的 cert_sha256：两种部署布局都要给得出指纹。

背景（2026-11-08 值守批发现的缺口，本文件是它的回归钉）：

- 老实现只读 `PROJECT_ROOT/certs/cert.pem`（仓库 / NSSM 时代布局）。
- 安装器布局（`C:/lanshare`）里没有这个文件 —— Caddy 管理 PKI，叶子证书按
  主机名分目录放在 `certs/caddy/certificates/local/<host>/<host>.crt`。
- 于是生产上 `cert_sha256` 恒为空串，而 App 端 `auto_switch._probeAddress`
  对空指纹是**直接拒绝**（`if (fp.isEmpty) return null`）→ 指纹门槛永远
  失败 → 「DHCP 换址后自动再发现」这条链路从 v4.0.x 起就是死的。

本文件同时钉住三条设计约束：
1. 指纹必须跟 **请求 Host** 走 —— App 的 TOFU 存的是握手时实际看到的叶子
   证书，同一台机器不同地址看到的是不同叶子，回错了同源复核照样拒绝。
2. Host 是客户端可控字段，拼路径前必须过字符集闸 + 解析后仍在证书目录内。
3. 给了 host 却找不到对应证书 → 空串（fail-closed），**不许**退到别的网卡
   或根 CA 的证书：那会把「明确没有」变成「有但不对」，现场更难判断。

测试里的地址一律用 RFC 5737 文档保留段（192.0.2.0/24），**不要**写成真实内网 IP
—— 公开仓库的 `scripts/pre-open-source-check.py` 会把它判成泄漏
（2026-11-08 发版时真的被拦下来了）。
"""
import base64
import hashlib
import textwrap

import pytest


def _pem(payload: bytes) -> bytes:
    """把任意字节包成一张格式合法的 PEM 外壳（不要求是真证书）。"""
    body = "\n".join(textwrap.wrap(base64.b64encode(payload).decode(), 64))
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n".encode("ascii")


def _fp(payload: bytes) -> str:
    """独立算一遍期望指纹，不复用被测实现的代码路径。"""
    return ":".join(f"{b:02X}" for b in hashlib.sha256(payload).digest())


LEAF_BYTES = b"leaf-cert-payload-192-168-1-12"
LEAF_FP = _fp(LEAF_BYTES)


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    """每个用例都从空缓存、空 entries 缓存开始（模块级全局，不隔离会串味）。"""
    import app.api.appver as m
    m._CFP_CACHE.clear()
    m._ENTRIES_CACHE.clear()
    yield
    m._CFP_CACHE.clear()
    m._ENTRIES_CACHE.clear()


def _installer_layout(tmp_path, host: str = "192.0.2.10", payload: bytes = LEAF_BYTES):
    """造一个安装器布局：没有 certs/cert.pem，只有 Caddy 叶子证书。"""
    d = tmp_path / "certs" / "caddy" / "certificates" / "local" / host
    d.mkdir(parents=True)
    (d / f"{host}.crt").write_bytes(_pem(payload))
    return tmp_path


class TestLeafCertPath:
    def test_repo_layout_prefers_legacy_cert_pem(self, monkeypatch, tmp_path):
        """仓库布局：certs/cert.pem 在 → 用它（行为与修复前一致，不许回归）。"""
        import app.api.appver as m
        (tmp_path / "certs").mkdir()
        legacy = tmp_path / "certs" / "cert.pem"
        legacy.write_bytes(_pem(b"legacy-server-cert"))
        monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
        assert m._cert_fingerprint("192.0.2.10") == _fp(b"legacy-server-cert")

    def test_installer_layout_finds_leaf_by_host(self, monkeypatch, tmp_path):
        """安装器布局：按 Host 找到 Caddy 叶子证书 ← 修复的核心。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        assert m._cert_fingerprint("192.0.2.10") == LEAF_FP

    def test_host_case_and_port_insensitive(self, monkeypatch, tmp_path):
        """Host 大小写、残留端口不该让查找失败。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        assert m._cert_fingerprint("192.0.2.10") == LEAF_FP

        class _Req:
            headers = {"host": "192.0.2.10:8765"}   # 带端口

        assert m._request_host(_Req()) == "192.0.2.10"

        class _Req2:
            headers = {"host": "LOCALHOST:8765"}

        assert m._request_host(_Req2()) == "localhost"

    def test_unknown_host_returns_empty_not_fallback(self, monkeypatch, tmp_path):
        """给了 host 但没这张证书 → 空串，绝不退到别的证书（fail-closed）。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        assert m._cert_fingerprint("10.0.0.99") == ""

    def test_no_host_installer_layout_returns_empty(self, monkeypatch, tmp_path):
        """安装器布局 + 拿不到 Host：宁可空串也不猜一张回去。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        assert m._cert_fingerprint(None) == ""

    @pytest.mark.parametrize("bad", [
        "../../../etc/passwd",
        "..",
        "a/b",
        "host\nInjected: 1",
        "",
        "x" * 254,
        "host with space",
    ])
    def test_unsafe_host_rejected(self, monkeypatch, tmp_path, bad):
        """Host 是客户端可控字段：非法字符/超长一律拒绝，且不抛异常。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        assert m._cert_fingerprint(bad) == ""

    def test_traversal_cannot_escape_cert_dir(self, monkeypatch, tmp_path):
        """纵深：即使字符串过了字符集闸，解析后跑出证书目录也必须拒绝。

        ⚠️ 逃逸落点必须**真的放一张证书**：2026-11-08 消融实证，夹具不放
        「host='..' 词法归一后的落点文件」时，把防线整段删掉 19 项仍全绿
        （被「目标不存在 → None」掩蔽）。放上去之后删防线必须红。
        """
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        # host='..' → cand=local/../​...crt → Win32 词法归一落点 = certificates/...crt。
        # 在落点放一张真 PEM：防线在 → None；防线删 → is_file() 命中、指纹非空。
        escaped = root / "certs" / "caddy" / "certificates" / "...crt"
        escaped.write_bytes(_pem(b"escaped-cert-must-never-be-served"))
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        assert m._leaf_cert_path("..") is None
        assert m._cert_fingerprint("..") == ""
        assert m._leaf_cert_path("local") is None   # 目录不是文件 → None（原恒真断言改正）

    def test_two_hosts_get_different_fingerprints(self, monkeypatch, tmp_path):
        """同一台机器两张叶子证书：指纹必须各自独立，缓存不能串。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path, "192.0.2.10", b"cert-a")
        d = root / "certs" / "caddy" / "certificates" / "local" / "192.0.2.11"
        d.mkdir(parents=True)
        (d / "192.0.2.11.crt").write_bytes(_pem(b"cert-b"))
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        a = m._cert_fingerprint("192.0.2.10")
        b = m._cert_fingerprint("192.0.2.11")
        assert a == _fp(b"cert-a")
        assert b == _fp(b"cert-b")
        assert a != b
        # 再取一次走缓存，值不变
        assert m._cert_fingerprint("192.0.2.10") == a

    def test_cache_invalidated_when_cert_rotates(self, monkeypatch, tmp_path):
        """证书轮换（内容变、mtime 变）后必须重新算，不能吃旧缓存。"""
        import app.api.appver as m
        root = _installer_layout(tmp_path)
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        first = m._cert_fingerprint("192.0.2.10")
        crt = root / "certs" / "caddy" / "certificates" / "local" / "192.0.2.10" / "192.0.2.10.crt"
        crt.write_bytes(_pem(b"rotated-cert-payload!"))
        import os
        st = crt.stat()
        os.utime(crt, (st.st_atime, st.st_mtime + 10))
        assert m._cert_fingerprint("192.0.2.10") == _fp(b"rotated-cert-payload!")
        assert m._cert_fingerprint("192.0.2.10") != first


class TestEntriesEndpoint:
    """端点层：Host 不同的请求拿到的是各自那张叶子证书的指纹。"""

    def _call(self, monkeypatch, root, host):
        import app.api.appver as m
        monkeypatch.setattr(m, "PROJECT_ROOT", root)
        monkeypatch.setattr(m, "_lan_addresses", lambda: ["192.0.2.10"])

        class _Req:
            headers = {"host": host}

        m._ENTRIES_CACHE.clear()
        return m.app_entries(request=_Req())

    def test_fingerprint_follows_request_host(self, monkeypatch, tmp_path):
        root = _installer_layout(tmp_path)
        assert self._call(monkeypatch, root, "192.0.2.10:8765")["cert_sha256"] == LEAF_FP

    def test_cached_entries_still_recompute_fingerprint(self, monkeypatch, tmp_path):
        """整包缓存命中时，指纹仍要按本次 Host 现算（否则第二个客户端必挂）。"""
        root = _installer_layout(tmp_path)
        d = root / "certs" / "caddy" / "certificates" / "local" / "localhost"
        d.mkdir(parents=True)
        (d / "localhost.crt").write_bytes(_pem(b"localhost-leaf"))
        first = self._call(monkeypatch, root, "192.0.2.10:8765")
        assert first["cert_sha256"] == LEAF_FP
        # 未清缓存再打一次，换 Host
        second = self._call(monkeypatch, root, "localhost:8765")
        assert second["cert_sha256"] == _fp(b"localhost-leaf")

    def test_no_cert_still_serves_entries(self, monkeypatch, tmp_path):
        """没证书不能让整个端点挂 —— 其余字段照常下发。"""
        import app.api.appver as m
        monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(m, "_lan_addresses", lambda: ["192.0.2.10"])
        m._ENTRIES_CACHE.clear()

        class _Req:
            headers = {"host": "192.0.2.10:8765"}

        body = m.app_entries(request=_Req())
        assert body["cert_sha256"] == ""
        assert body["lan"] == ["192.0.2.10"]

    def test_request_without_host_header(self, monkeypatch, tmp_path):
        """请求没带 Host（HTTP/1.0 等）：按 None 处理，返回空串不炸。"""
        import app.api.appver as m
        monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(m, "_lan_addresses", lambda: [])
        m._ENTRIES_CACHE.clear()

        class _Req:
            headers: dict = {}

        body = m.app_entries(request=_Req())
        assert body["cert_sha256"] == ""
