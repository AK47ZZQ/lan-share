"""并发对抗测试（R1-P2-4 的并发补证，2026-09-13 安全审查批）。

R1-P2-4 把下载计数移到 prepare 发票时并原子把关，此前只有**序列**测试
（test_baseline.test_share_download_limit）——并发场景（提示词明确要求的
对抗面）从未被证明：N 个线程同时发票，"限 3 次"是否恰好只发 3 张？

设计：每线程独立 TestClient（starlette TestClient 单实例不保证线程安全），
共享同一会话 Cookie（会话在 DB，多线程同权合法），真并发打 prepare。
验收：codes.count(200)==3 且 410==5。消融：consume_download 的条件 UPDATE
退化为无条件递增时本测试必红（发票数 > 3）。
"""
import concurrent.futures

from fastapi.testclient import TestClient

from app.main import app


def test_prepare_concurrent_quota(auth, workdir):
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("concurrent.txt", b"x", "text/plain")})
    r = auth.post("/api/share", data={"path": f"{workdir}/concurrent.txt",
                                      "max_downloads": "3"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    def attempt(_):
        c = TestClient(app, base_url="https://testserver")
        c.cookies.update(auth.cookies)
        return c.post(f"/api/share/{token}/prepare", json={}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(attempt, range(8)))
    assert codes.count(200) == 3, codes
    assert codes.count(410) == 5, codes
