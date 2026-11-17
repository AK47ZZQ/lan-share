"""2026-11-17 五轮对抗测试：钉住本批修复。

每条对应一个具体 PoC（其中两条是 review 子 agent 用 TestClient 实测复现过的），
修完必须证明 PoC 失效 —— 不是「我觉得修好了」。

分轮：
  R1 认证与授权     无凭据 / 伪造 Token / 登出吊销 / 越权
  R2 路径与文件系统  尾随点 / 8.3 短名（读写两个方向）/ 编码遍历 / 绝对路径
  R3 注入            SQL / LIKE 通配符 / Host 头
  R4 更新链路        清单篡改 / 降级 / 大小不符
  R5 资源耗尽        超长输入 / 超大分页 / 并发同名
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.filetools import clean_filename, safe_path
from app.spaces import _clean_parts


# ── 轮 2：路径与文件系统（纯函数，最稳的一层）────────────────────
class TestPathTraversal:
    """尾随点绕过是本轮最重的 P0：普通成员能读他人私人空间，还能投递 APK。"""

    @pytest.mark.parametrize("raw", [
        "private./alice",           # 尾随点
        "private. /alice",          # 尾随点 + 空格
        "private.../alice",         # 多点
        "incoming./alice",          # 同类保留段
        "App安装包./evil.apk",       # 管理员专属目录
        "tmp./x",
    ])
    def test_trailing_dot_rejected(self, raw):
        with pytest.raises(HTTPException) as ei:
            _clean_parts(raw)
        assert ei.value.status_code == 403

    @pytest.mark.parametrize("raw", [
        "App安装包./evil.apk",
        "a.txt.",
        "shared/team./f",
    ])
    def test_safe_path_trailing_dot_rejected(self, raw):
        with pytest.raises(HTTPException) as ei:
            safe_path(raw)
        assert ei.value.status_code == 403

    def test_safe_path_normal_still_works(self):
        """正常路径必须照常放行 —— 这条是上面那组的对照，防修复过度。"""
        out = safe_path("private/alice/secret.txt")
        assert out.name == "secret.txt"

    @pytest.mark.parametrize("raw", [
        "../etc/passwd",
        "..\\windows\\win.ini",
        "shared/../../private/alice",
        "....//....//x",
    ])
    def test_traversal_rejected(self, raw):
        with pytest.raises(HTTPException):
            _clean_parts(raw)

    @pytest.mark.parametrize("raw", [
        ".trash/x", ".meta.db", ".lanshare-pgpass",  # 点前缀隐藏
    ])
    def test_dot_prefix_rejected(self, raw):
        with pytest.raises(HTTPException):
            _clean_parts(raw)

    def test_normal_paths_still_work(self):
        """修复不能误伤正常路径 —— 否则家里人连自己文件都打不开。"""
        assert _clean_parts("private/alice/photo.jpg") == ["private", "alice", "photo.jpg"]
        assert _clean_parts("shared/team/report.pdf") == ["shared", "team", "report.pdf"]
        assert _clean_parts("incoming/bob/a.txt") == ["incoming", "bob", "a.txt"]

    # 8.3 短名：读早就封了，写是本轮补的
    @pytest.mark.parametrize("name", [
        "LANSHA~1", "META~1.DB", "TRASH~1", "TEAM-S~1",
    ])
    def test_83_alias_in_filename_neutralized(self, name):
        """上传文件名带 8.3 短名形态时必须被改掉，不能落到 .lanshare-pgpass 上。"""
        out = clean_filename(name)
        assert "~" not in out, f"{name} → {out} 仍含短名形态"

    def test_normal_filename_untouched(self):
        assert clean_filename("报告.pdf") == "报告.pdf"
        assert clean_filename("a.txt") == "a.txt"


# ── 轮 3：注入 ────────────────────────────────────────────────
class TestInjection:
    @pytest.mark.parametrize("evil", [
        "admin'--",
        "admin' OR '1'='1",
        "'; DROP TABLE users; --",
        "admin\x00",
    ])
    def test_sql_metachars_in_path_rejected(self, evil):
        """路径里的 SQL 元字符即使进不了 SQL（全参数化），也不该被当普通文件名放行到离谱程度。
        这里的断言是「不抛 UnboundLocal 之外的意外」——主要防的是拼接回归。"""
        # 路径段里含单引号是合法文件名，关键是不能抛非 HTTPException 的异常
        try:
            _clean_parts(evil)
        except HTTPException:
            pass
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"非预期异常类型 {type(e).__name__}: {e}")


# ── 轮 5：资源耗尽 ────────────────────────────────────────────
class TestResourceExhaustion:
    def test_extremely_long_segment_does_not_crash(self):
        """超长路径段应该被拒或正常处理，不能抛 RecursionError/MemoryError。"""
        huge = "a" * 5000
        try:
            _clean_parts(huge)
        except HTTPException:
            pass
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"超长段引发 {type(e).__name__}: {e}")

    def test_filename_truncated_to_200(self):
        out = clean_filename("x" * 5000 + ".txt")
        assert len(out) <= 200

    def test_filename_trailing_dot_after_truncation(self):
        """截断后重新落出结尾点会被 NTFS 静默剥离 → 登记名与磁盘名分叉。"""
        out = clean_filename("a" * 199 + ".")
        assert not out.endswith(".") and not out.endswith(" ")


# ── 轮 4：更新链路的服务端侧 ──────────────────────────────────
class TestUpdateChainServerSide:
    def test_appver_rejects_non_https(self):
        """/api/app/latest 里的 url 若来自自定义配置，协议必须是 https。"""
        from app.api import appver
        # 该模块的 URL 白名单函数（存在则测，不存在跳过——不假装测了）
        fn = getattr(appver, "_is_https_url", None)
        if fn is None:
            pytest.skip("appver 无独立 URL 校验函数")
        assert fn("https://dl.ak47zzq.com/a.apk") is True
        assert fn("http://dl.ak47zzq.com/a.apk") is False
        assert fn("file:///etc/passwd") is False


# ── API 级对抗（真发请求，不是纯函数推演）────────────────────────
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
# ⚠️ 不要 ：那会把 conftest 当**普通模块**再执行
# 一遍，而它模块级有 os.environ["SHARE_ROOT"] = tempfile.mkdtemp() —— 于是
# 又生出一个新临时目录，而 app.config 里的 SHARE_ROOT 早已绑定到旧值，两边
# 分叉，后续所有落盘断言都找不到文件（2026-11-17 实测：把 test_baseline 的
# 4 条 curl 类测试拖红）。常量就地抄一份，与 conftest 保持一致即可。
ADMIN_USER = "admin"
ADMIN_PASS = "test-pass-123"  # 一次性内存实例，非真实凭据


class TestAuthBypassApi:
    def test_logout_revokes_bearer_token(self, client):
        """登出必须吊销 Bearer。原来只删 Cookie 会话 —— review 实测：登出后
        同一个设备 Token 调 /api/auth/me 仍返回 200，且还能用 90 天（滑动续期）。
        App 的唯一认证方式就是这个 Token，不吊销等于用户点了退出却没退。
        """
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200, r.text
        r = c.post("/api/auth/token", json={"name": "pytest-adversarial"})
        assert r.status_code == 200, r.text
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}

        assert c.get("/api/auth/me", headers=h).status_code == 200, "签发后应可用"
        assert c.post("/api/logout", headers=h).status_code == 200
        assert c.get("/api/auth/me", headers=h).status_code == 401, "登出后 Token 仍有效！"

    def test_forged_bearer_rejected(self, client):
        h = {"Authorization": "Bearer not-a-real-token-0123456789"}
        assert client.get("/api/auth/me", headers=h).status_code == 401

    def test_trailing_dot_via_real_api(self, auth):
        """review 实测过的 PoC：普通成员 GET /files/private./alice/secret.txt
        曾返回 200。现在必须被拦。"""
        for p in (
            "/api/files/private./admin",
            "/api/files/App安装包.",
            "/api/files/incoming./admin",
        ):
            r = auth.get(p)
            assert r.status_code in (403, 404), f"{p} → {r.status_code}（应拒绝）"

    def test_local_requires_loopback_host(self, client):
        """/local/* 的 Host 伪造：原来填个私网 Host 就能让整组接口对公网敞开
        （review 实测：两步把整库 pg_dump 到攻击者 SMB）。"""
        c = TestClient(app, base_url="https://testserver")  # Host = testserver
        r = c.get("/local/health")
        assert r.status_code == 403, f"非回环 Host 被放行：{r.status_code}"

    def test_local_allows_real_loopback(self, client):
        """对照：托盘真实形态（直连 127.0.0.1:8766）必须照常能用。"""
        # base_url 只改 Host，来源 IP 还得单独造 —— _require_local 两道都要
        c = TestClient(app, client=("127.0.0.1", 51000),
                       base_url="http://127.0.0.1:8766")
        r = c.get("/local/health")
        assert r.status_code == 200, r.text
