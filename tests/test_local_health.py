"""`/local/health` 的测试：托盘控制面板的健康快照。

为什么这个端点值得一个专属测试文件：

1. **它是免登录端点**——安全全靠 `_require_local`（环回 + 隧道标记双重防线）。
   这组防线在 /local/backup 与 /local/public-url 上各有测试，但每个新端点
   都必须自己钉一遍（v3.56 rowid 事故的镜像教训：没被执行过的防线，连变红
   的机会都没有）。
2. **形状契约**：托盘 C#/JS 按固定键名渲染，键名漂移 = 面板卡片静默空白。
3. **降级语义**：单表挂了不能让整个健康快照 500——面板拿不到数据会永远
   显示"读取中…"，比显示旧数据更糟。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.healthreport import counts_snapshot, router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    # TestClient 默认 host="testclient"（外部直连形态）
    return TestClient(app)


class TestAccessControl:
    """免登录端点，防线必须自己钉。"""

    def test_blocked_for_remote(self, client):
        assert client.get("/local/health").status_code == 403

    def test_spoofed_real_ip_ignored(self, client):
        """非回环直连时伪造 X-Real-IP 必须无效。"""
        resp = client.get("/local/health", headers={"X-Real-IP": "127.0.0.1"})
        assert resp.status_code == 403

    def test_public_marker_blocks_loopback(self):
        """FRP 隧道形态：物理回环 + 公网标记 → 必须拒。

        链路：公网 → frps → frpc(本机) → Caddy → 127.0.0.1:8766。
        """
        from app.api import local_config

        app = FastAPI()
        app.include_router(router)
        app.include_router(local_config.router)
        tc = TestClient(app, client=("127.0.0.1", 51234))
        resp = tc.get("/local/health", headers={"X-LanShare-Entry": "public"})
        assert resp.status_code == 403

    def test_loopback_allowed(self):
        """放行路径：真环回（无隧道标记）→ 200 + 形状契约。

        TestClient(client=("127.0.0.1", 51234)) 造出「真环回直连」形态，
        `_require_local` 应放行。
        """
        app = FastAPI()
        app.include_router(router)
        tc = TestClient(app, client=("127.0.0.1", 51234))
        resp = tc.get("http://t/local/health")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("ts", "disk_used_pct", "disk_free_gb", "loops_failing",
                    "backup_freshness", "counts"):
            assert key in data, f"形状契约缺键 {key} —— 托盘渲染会静默空白"


class TestCountsDegradation:
    """单表挂了不能让健康快照 500——面板显示"读取中…"比旧数据更糟。"""

    def test_single_store_failure_degrades_to_none(self, monkeypatch):
        from app.store import photos as photos_store

        def _boom():
            raise RuntimeError("pg down")

        monkeypatch.setattr(photos_store, "count", _boom)
        counts = counts_snapshot()
        assert counts["photos"] is None
        assert counts["users"] is not None  # 其他表不受影响

    def test_all_stores_failure_still_returns(self, monkeypatch):
        import app.store.devices as devices_store
        import app.store.photos as photos_store
        import app.store.shares as shares_store
        import app.store.users as users_store

        def _boom():
            raise RuntimeError("pg down")

        for mod in (users_store, photos_store, shares_store, devices_store):
            monkeypatch.setattr(mod, "count", _boom)
        counts = counts_snapshot()
        assert counts == {"users": None, "photos": None, "shares": None, "devices": None}
