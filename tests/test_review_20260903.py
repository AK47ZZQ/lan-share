"""回归：2026-09-03 十轮 review + 五轮对抗审查修复批。

覆盖四项后端修复：
1. /api/move 移动目录后 grants.path 同步（P1：rename 后 is_dir 恒 False 的死代码）
2. /api/copy 配额定价走 resolve（P2：裸 src 越界路径不被当文件系统路径遍历）
3. /api/upload 直传配额不信 multipart 谎报 size（P2，仅验证不崩+正常路径）
4. visible() 空段防御（P3）

注意路径语义：裸路径即 shared 空间物理路径（"shared/x" 是根下名为 shared 的
目录）；授权 vkey = "shared/" + 物理相对路径。要观察到 move 的授权同步，
必须把目录移进子目录（移到根的 rel 不变 → vkey 本就不变）。
"""
import app.api.system as _system
import server
from fastapi.testclient import TestClient


def _mk_user(auth, name, role):
    auth.post("/api/admin/invites", json={"role": role, "hours": 1})
    inv = [i for i in auth.get("/api/admin/invites").json() if not i["used"]][0]["code"]
    assert auth.post("/api/invites/redeem", json={"code": inv, "username": name,
                                                  "password": "member-pass-123"}).status_code == 200
    c = TestClient(server.app, base_url="https://testserver")
    assert c.post("/api/login", json={"username": name, "password": "member-pass-123"}).status_code == 200
    uid = [u["id"] for u in auth.get("/api/admin/users").json() if u["username"] == name][0]
    return c, uid


def _make_member(auth, name):
    auth.post("/api/admin/invites", json={"role": "member", "hours": 1})
    inv = [i for i in auth.get("/api/admin/invites").json() if not i["used"]][0]["code"]
    r = auth.post("/api/invites/redeem", json={"code": inv, "username": name,
                                               "password": "member-pass-123"})
    assert r.status_code == 200, r.text
    uid = [u["id"] for u in auth.get("/api/admin/users").json()
           if u["username"] == name][0]
    return uid


def test_move_dir_syncs_grants(client, auth):
    """P1 回归：把带授权的目录移进另一个目录，grants.path 必须跟随新位置。"""
    assert auth.post("/api/mkdir/rv-move-src/sub").status_code == 200
    assert auth.post("/api/mkdir/rv-move-dst").status_code == 200
    uid = _make_member(auth, "rv-move-u")
    for gp in ("rv-move-src", "rv-move-src/sub"):   # 顶层 + 子树各一条
        r = auth.post("/api/admin/grants", json={"user_id": uid, "path": gp,
                                                 "can_read": True, "can_write": True})
        assert r.status_code == 200, r.text
    r = auth.post("/api/move", json={"sources": ["rv-move-src"], "dest_dir": "rv-move-dst"})
    assert r.status_code == 200 and r.json()["results"][0]["ok"], r.text
    paths = [g["path"] for g in auth.get("/api/admin/grants").json()
             if "rv-move" in g["path"]]
    assert "shared/rv-move-src" not in paths and "shared/rv-move-src/sub" not in paths,         f"授权未同步：{paths}"
    assert "shared/rv-move-dst/rv-move-src" in paths, f"顶层授权未同步：{paths}"
    assert "shared/rv-move-dst/rv-move-src/sub" in paths, f"子树授权未同步：{paths}"


def test_rename_dir_syncs_grants(client, auth):
    """rename 路径（old_rel 在 rename 前取得）原本就正确，回归锁住。"""
    assert auth.post("/api/mkdir/rv-ren-proj").status_code == 200
    uid = _make_member(auth, "rv-ren-u")
    assert auth.post("/api/admin/grants", json={
        "user_id": uid, "path": "rv-ren-proj",
        "can_read": True, "can_write": True}).status_code == 200
    assert auth.post("/api/rename", json={
        "path": "rv-ren-proj", "new_name": "rv-ren-proj2"}).status_code == 200
    paths = [g["path"] for g in auth.get("/api/admin/grants").json()]
    assert "shared/rv-ren-proj2" in paths, paths


def test_copy_quota_pricing_no_traversal(client, auth, workdir):
    """P2 回归：copy 定价对 ".."/绝对路径等非法 src 不做文件系统遍历，
    逐项报错但整体 200。"""
    auth.post(f"/api/upload/{workdir}", files={
        "file": ("a.txt", b"hello")})
    r = auth.post("/api/copy", json={
        "sources": ["../outside", "C:/Windows/System32", f"{workdir}/a.txt"],
        "dest_dir": workdir})
    assert r.status_code == 200
    res = {x["src"]: x for x in r.json()["results"]}
    assert not res["../outside"]["ok"]
    assert not res["C:/Windows/System32"]["ok"]
    assert res[f"{workdir}/a.txt"]["ok"]


def test_grant_prefix_matches_subdirs(auth, workdir):
    """P1 回归（Windows normcase 反斜杠缺陷）：授权前缀必须命中子目录。
    guest 无默认读权（E4）——授予 shared:<dir> 后 <dir>/sub 下的文件必须可见，
    member 已授权目录的子目录可写入。"""
    auth.post(f"/api/mkdir/{workdir}/gpre/sub")
    guest, uid = _mk_user(auth, "rv-gpre-g", "guest")
    assert auth.post("/api/admin/grants", json={
        "user_id": uid, "path": f"{workdir}/gpre",
        "can_read": True, "can_write": False}).status_code == 200
    # 精确目录可读（存量已覆盖，锁定）
    assert guest.get(f"/api/files/{workdir}/gpre").status_code == 200
    # 子目录必须也可读（此前 Windows 上 403）
    assert guest.get(f"/api/files/{workdir}/gpre/sub").status_code == 200
    # 成员写授权前缀：grant 目录的子目录可写入
    member, uid2 = _mk_user(auth, "rv-gpre-m", "member")
    assert auth.post("/api/admin/grants", json={
        "user_id": uid2, "path": f"{workdir}/gpre",
        "can_read": True, "can_write": True}).status_code == 200
    r = member.post("/api/upload/init", json={
        "dest_dir": f"{workdir}/gpre/sub", "filename": "m.txt", "size": 10})
    assert r.status_code == 200, r.text


def test_visible_empty_parts_false():
    from app.spaces import visible
    u = {"role": "admin", "username": "x", "user_id": 1}
    assert visible(u, []) is False
