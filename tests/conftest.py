"""M0 pytest 基线夹具。

关键约束：
- SHARE_ROOT 必须在导入 app 包前设好（app.config 模块级读 env 定 DB_PATH），
  因此整个测试会话共用一个临时数据根，各测试用唯一子目录隔离。
- TestClient 不进 with 块 → lifespan 不触发 → 定时清理线程不会启动（测试确定性）。
- 限速/防爆破/票据是进程内存态，跨测试会串味：每个测试前经各模块的
  官方 reset 函数清空（07 审查批次 2 测试解钉：不再直掏私有字典）。
"""

import os
import shutil
import tempfile

import pytest

# ── 必须先于导入 app 包 ──────────────────────────────────
_ROOT = tempfile.mkdtemp(prefix="lan-share-test-")
os.environ["SHARE_ROOT"] = _ROOT

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.api import auth as _auth, photos as _photos, share as _share  # noqa: E402
from app.security import reset_auth_gates  # noqa: E402
from app.services import kb as _kb  # noqa: E402

ADMIN_USER = "admin"
ADMIN_PASS = "test-pass-123"
MEMBER_PASS = "member-pass-123"    # 测试成员默认口令（一次性内存实例，非真实凭据）
MEMBER_PASS2 = "pass-12345678"      # 同上：多用户测试备用口令


@pytest.fixture(scope="session")
def client():
    """匿名客户端：setup 后清掉 Cookie，保证"未登录"断言纯净。"""
    c = TestClient(app, base_url="https://testserver")
    if not _auth.user_count():
        r = c.post("/api/setup", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200, r.text
    c.cookies.clear()
    return c


@pytest.fixture(autouse=True)
def _reset_in_memory_state():
    reset_auth_gates()
    _share.reset_share_state()
    _auth.reset_pair_codes()
    _photos.reset_scan_throttle()
    _kb.reset_kb_state()
    yield
    reset_auth_gates()
    _share.reset_share_state()
    _auth.reset_pair_codes()
    _kb.reset_kb_state()


@pytest.fixture()
def auth(client):
    """独立实例上的已登录客户端——不能在共享的匿名 client 上登录，
    否则 Cookie 会污染其它测试的"未登录"断言。"""
    c = TestClient(app, base_url="https://testserver")
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    c.username = ADMIN_USER
    return c


@pytest.fixture()
def workdir():
    """每个测试独立的根下子目录，测试结束清理。"""
    name = f"t-{os.urandom(4).hex()}"
    path = os.path.join(_ROOT, name)
    os.makedirs(path)
    yield name
    shutil.rmtree(path, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_ROOT, ignore_errors=True)
