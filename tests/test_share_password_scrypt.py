"""分享提取码哈希升级（sha256 单轮 → scrypt，2026-09-25）。

覆盖：新分享落库 scrypt、旧格式行兼容校验并惰性升级、错误密码不升级、
畸形哈希 fail-closed（403 而非 500）。防爆破锁定行为由
test_baseline.py::test_share_password_lock 继续钉住（创建侧已切新格式，
该用例本身即"scrypt 路径防爆破"的回归）。
"""
import hashlib
import secrets
import time

from app.db import _db
from app.security import verify_password
from app.store import shares as shares_store


def _legacy_hash(password: str) -> str:
    """旧格式：<salt_hex>$<sha256(salt+pwd)>（与升级前 share.py:81 一致）。"""
    salt = secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.sha256(salt + password.encode()).hexdigest()


def _insert_share(token: str, password_hash: str | None) -> None:
    """直接落一行分享（prepare 不触盘，无需真实文件）。"""
    shares_store.create(token, "2026/09/legacy.txt", "legacy.txt", 1,
                        time.time(), time.time() + 3600, password_hash,
                        None, None, None)


def _stored_hash(token: str) -> str | None:
    return shares_store.get_by_token(token)["password_hash"]


def test_new_share_stores_scrypt_hash(auth, client, workdir):
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("scryptme.txt", b"x", "text/plain")})
    r = auth.post("/api/share", data={"path": f"{workdir}/scryptme.txt",
                                      "password": "1234"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    stored = _stored_hash(token)
    assert stored.startswith("scrypt$"), stored
    assert verify_password("1234", stored)

    # 契约不变：错误密码 403，正确密码换票成功
    assert client.post(f"/api/share/{token}/prepare",
                       json={"password": "0000"}).status_code == 403
    prep = client.post(f"/api/share/{token}/prepare", json={"password": "1234"})
    assert prep.status_code == 200, prep.text


def test_legacy_row_verifies_and_lazily_upgrades(client):
    token = secrets.token_urlsafe(16)
    _insert_share(token, _legacy_hash("9999"))
    prep = client.post(f"/api/share/{token}/prepare", json={"password": "9999"})
    assert prep.status_code == 200, prep.text

    upgraded = _stored_hash(token)
    assert upgraded.startswith("scrypt$"), upgraded
    assert verify_password("9999", upgraded)
    # 升级后同一密码继续可取件（链接对家人保持可用）
    prep2 = client.post(f"/api/share/{token}/prepare", json={"password": "9999"})
    assert prep2.status_code == 200, prep2.text


def test_legacy_row_wrong_password_keeps_hash(client):
    token = secrets.token_urlsafe(16)
    legacy = _legacy_hash("8888")
    _insert_share(token, legacy)
    resp = client.post(f"/api/share/{token}/prepare", json={"password": "7777"})
    assert resp.status_code == 403
    assert _stored_hash(token) == legacy   # 校验失败绝不改写哈希


def test_malformed_hash_fails_closed(client):
    token = secrets.token_urlsafe(16)
    _insert_share(token, "garbage-without-separator")
    resp = client.post(f"/api/share/{token}/prepare", json={"password": "1234"})
    assert resp.status_code == 403   # 不是 500


def test_legacy_row_survives_db_roundtrip_via_raw_sql(client):
    """从 _db 直读（而非 store 层）复核升级确实落盘——防止测试与实现共用
    同一读路径而空转。"""
    token = secrets.token_urlsafe(16)
    _insert_share(token, _legacy_hash("6666"))
    client.post(f"/api/share/{token}/prepare", json={"password": "6666"})
    row = _db().execute("SELECT password_hash FROM shares WHERE token=?",
                        (token,)).fetchone()
    assert row["password_hash"].startswith("scrypt$")


def test_legacy_row_concurrent_prepare_single_writer(client):
    """对抗审查 P1-1/P2-4：并发 prepare 同一旧格式行——全部 200、CAS 保证
    单一写者、最终哈希可用。"""
    import concurrent.futures as cf
    token = secrets.token_urlsafe(16)
    _insert_share(token, _legacy_hash("5555"))
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        sts = list(pool.map(
            lambda _: client.post(f"/api/share/{token}/prepare",
                                  json={"password": "5555"}).status_code,
            range(6)))
    assert all(s == 200 for s in sts), sts
    row = _db().execute("SELECT password_hash FROM shares WHERE token=?",
                        (token,)).fetchone()
    assert row["password_hash"].startswith("scrypt$")
    assert verify_password("5555", row["password_hash"])


def test_cas_write_requires_matching_expect(client):
    """CAS 写方法：expect 不匹配 ⇒ 0 行改写（旧哈希保持原样）。"""
    token = secrets.token_urlsafe(16)
    legacy = _legacy_hash("4444")
    _insert_share(token, legacy)
    n = shares_store.set_password_hash_if(token, "scrypt$X", "不是这行")
    assert n == 0
    assert _stored_hash(token) == legacy
    n = shares_store.set_password_hash_if(token, "scrypt$Y", legacy)
    assert n == 1
    assert _stored_hash(token) == "scrypt$Y"


def test_share_fails_empty_key_recycled(monkeypatch):
    """15-路线图 P3：锁定窗口滑出后的空队列键即时回收（防 token 维度只增不减）。"""
    import time as _time

    from app.api import share as sh
    tok = f"t-{secrets.token_hex(4)}"
    sh._share_fails[tok] = [_time.time() - 10**6]
    monkeypatch.setattr(sh, "LOGIN_LOCK_SECONDS", 0)
    assert sh._share_locked_remaining(tok) == 0
    assert tok not in sh._share_fails


def test_badpwd_audit_key_uses_path_not_name(client):
    """2026-11-08 夜批 L1 修复钉：badpwd/locked 审计键统一到 rel 路径——
    同名不同路径的两条分享，取件记录不得互相串线。
    必须走真实 API（prepare 错误密码）触发实现写审计，手工插行=测试空转
    （消融实证：手工插行形态下改坏实现钉仍绿）。"""
    rel = "2026/11/b/报告.pdf"
    tok = secrets.token_urlsafe(8)
    shares_store.create(tok, rel, "报告.pdf", 1, time.time(),
                        time.time() + 3600, "x" * 8, None, None, None)
    r = client.post(f"/api/share/{tok}/prepare", json={"password": "错的"})
    assert r.status_code == 403
    row = _db().execute(
        "SELECT target FROM audit_log WHERE action='share_badpwd' AND target=?",
        (rel,)).fetchone()
    assert row is not None, "badpwd 审计键必须是 rel 路径（同名混串修复钉）"


def test_share_history_success_path_executes_new_sql(client, auth):
    """v3.54 纪律补钉（2026-11-08 夜批）：share_history 成功路径此前零用例
    （只有越权负例）——修复后的新 SQL（download/badpwd/locked 三 action 按
    rel 路径单参数查询）必须被真实执行，否则 PG 门禁连"变红的机会都没有"。"""
    from app.security import audit
    rel = "2026/11/hist/记录.txt"
    tok = secrets.token_urlsafe(8)
    shares_store.create(tok, rel, "记录.txt", 1, time.time(),
                        time.time() + 3600, None, None, None, None)
    audit("share_download", rel)
    audit("share_badpwd", rel)
    audit("share_locked", rel, "30s")
    audit("share_download", "别的路径/别分享.bin")   # 噪声行，不得混入
    r = auth.get(f"/api/shares/{tok}/history")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 3, rows
    assert {x["action"] for x in rows} == {"share_download", "share_badpwd", "share_locked"}
    assert all(x["ip"] is not None for x in rows)
