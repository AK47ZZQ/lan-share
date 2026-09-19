"""回收站恢复命名回归（2026-09-19 对抗审查 F2）。

缺陷：restore 的冲突重命名只查主名（f.txt → f (恢复).txt），不查恢复名本身
是否已被占用——第三次恢复时 `shutil.move` 在 Windows 下 os.rename 必抛、
回退 copy2 **静默覆盖**已存在的「f (恢复).txt」（bug·27ce"Linux rename 静默
替换"的同族：预检缺失 + 回退路径覆盖）。测试自建自清，唯一前缀定位。
"""
import os
import secrets

from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.config import SHARE_ROOT
from app.db import _db
from app.main import app


def test_restore_never_overwrites_existing_restored_copy(auth, client):
    username = "tr" + secrets.token_hex(3)
    r = auth.post("/api/admin/invites", json={"role": "member", "hours": 1})
    code = r.json()["code"]
    assert client.post("/api/invites/redeem", json={
        "code": code, "username": username, "password": MEMBER_PASS2}).status_code == 200
    m = TestClient(app, base_url="https://testserver")
    assert m.post("/api/login", json={
        "username": username, "password": MEMBER_PASS2}).status_code == 200

    base = f"private/{username}"
    fpath = os.path.join(SHARE_ROOT, "private", username, "tr-f.txt")
    orig = f"{base}/tr-f.txt"
    try:
        def plant(content):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)

        def delete_and_get_id():
            r = m.delete(f"/api/files/{orig}")
            assert r.status_code == 200, r.text
            row = _db().execute(
                "SELECT id FROM trash WHERE orig_path=? ORDER BY id DESC LIMIT 1",
                (orig,)).fetchone()
            return row["id"]

        # 两代同 orig_path 的回收站条目并存（删 A、再删 B），
        # 然后 f.txt 被 C 重新占用（不删除）——恢复必须避开占用名
        plant("A")
        id_a = delete_and_get_id()
        plant("B")
        id_b = delete_and_get_id()
        plant("C")

        # 恢复 A：f.txt 被占 → f (恢复).txt；恢复 B：恢复名也被占 → 不得覆盖
        r1 = m.post(f"/api/trash/{id_a}/restore")
        assert r1.status_code == 200, r1.text
        assert r1.json()["path"].endswith(" (恢复).txt")
        r2 = m.post(f"/api/trash/{id_b}/restore")
        assert r2.status_code == 200, r2.text
        p2 = r2.json()["path"]

        def read(p):
            with open(os.path.join(SHARE_ROOT, p), encoding="utf-8") as f:
                return f.read()

        assert read(f"{base}/tr-f.txt") == "C"          # 占位文件未被碰
        assert read(f"{base}/tr-f (恢复).txt") == "A"   # 关键：A 未被 B 静默覆盖
        assert p2 != f"{base}/tr-f.txt" and not p2.endswith(" (恢复).txt"), \
            f"第二次恢复撞进已占用落点：{p2}"
        assert read(p2) == "B"
    finally:
        row = _db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()
        # 测试名自清（回收站残条 + private 目录）
        _db().execute("DELETE FROM trash WHERE orig_path LIKE ?", (f"private/{username}/%",))
        _db().commit()
        import shutil
        shutil.rmtree(os.path.join(SHARE_ROOT, "private", username), ignore_errors=True)
