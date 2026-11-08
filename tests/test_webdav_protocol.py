"""WebDAV 协议最小集回归（B10 补覆盖：此前全库仅 1 条 PROPFIND 冒烟）。

覆盖五方法主链路：PUT → GET 内容一致、MKCOL → PROPFIND 列出、MOVE 重命名
（Destination 头 + 旧路径消失）、DELETE（进服务端回收站）、OPTIONS 能力头；
外加 Basic 鉴权拒绝与成员越权写他人 private 的 403（越权铁律在 DAV 侧的
同款收口）。客户端全部走 auth fixture（Cookie 会话过 _dav_auth）。
"""
import base64
import secrets

from conftest import MEMBER_PASS
from test_extras import _member_session, _rand


def test_dav_options_capabilities(auth):
    r = auth.request("OPTIONS", "/dav/")
    assert r.status_code == 200
    assert "PROPFIND" in r.headers.get("allow", "") or "DAV" in r.headers


def test_dav_put_get_roundtrip(auth):
    """PUT 落盘 → GET 原样取回（二进制一致）。"""
    name = f"davrt{_rand()}.txt"
    body = "协议往返 " + name
    r = auth.put(f"/dav/private/admin/{name}", content=body.encode("utf-8"))
    assert r.status_code in (200, 201, 204), r.text
    g = auth.get(f"/dav/private/admin/{name}")
    assert g.status_code == 200
    assert g.content == body.encode("utf-8")


def test_dav_mkcol_then_propfind_lists(auth):
    name = f"davdir{_rand()}"
    assert auth.request("MKCOL", f"/dav/private/admin/{name}").status_code in (200, 201)
    r = auth.request("PROPFIND", f"/dav/private/admin/{name}/", headers={"Depth": "1"})
    assert r.status_code == 207
    assert name in r.text
    # 重复建目录：405（RFC 4918 allow=405/403 均可，本项目语义 405）
    assert auth.request("MKCOL", f"/dav/private/admin/{name}").status_code in (405, 403)


def test_dav_move_rename_via_destination(auth):
    old = f"davmv{_rand()}.txt"
    new = f"davmv{_rand()}.txt"
    assert auth.put(f"/dav/private/admin/{old}", content=b"v1").status_code in (200, 201, 204)
    r = auth.request("MOVE", f"/dav/private/admin/{old}",
                     headers={"Destination": f"https://testserver/dav/private/admin/{new}"})
    assert r.status_code in (200, 201, 204), r.text
    assert auth.get(f"/dav/private/admin/{old}").status_code == 404
    assert auth.get(f"/dav/private/admin/{new}").status_code == 200


def test_dav_delete_goes_to_trash(auth):
    name = f"davdel{_rand()}.txt"
    assert auth.put(f"/dav/private/admin/{name}", content=b"bye").status_code in (200, 201, 204)
    r = auth.request("DELETE", f"/dav/private/admin/{name}")
    assert r.status_code in (200, 204)
    assert auth.get(f"/dav/private/admin/{name}").status_code == 404
    # 与界面删除同语义：文件进服务端回收站（.trash 有尸体），不物理消失
    trash_root = None
    from app.config import SHARE_ROOT
    cand = SHARE_ROOT / ".trash"
    if cand.is_dir():
        trash_root = cand
    assert trash_root is not None and any(trash_root.iterdir())


def test_dav_requires_credentials(client):
    cred = base64.b64encode(b"admin:wrong-pass").decode()
    r = client.request("PROPFIND", "/dav/",
                       headers={"Authorization": f"Basic {cred}", "Depth": "0"})
    assert r.status_code == 401


def test_dav_member_cannot_write_others_private(client):
    """越权铁律（铁律 41）在 DAV 写路径：成员 PUT 他人 private → 403。"""
    username = "dv" + _rand()
    from test_extras import _mk_member
    _mk_member(client, username, MEMBER_PASS)
    _member_session(client, username, MEMBER_PASS)
    other = base64.b64encode(f"{username}:{MEMBER_PASS}".encode()).decode()
    r = client.put("/dav/private/admin/hijack.txt",
                   headers={"Authorization": f"Basic {other}"}, content=b"x")
    # 403/409 均为拒绝语义（resolve 守卫对"成员写他人私人空间"给 409 跨空间码）
    assert r.status_code in (403, 404, 409)


def test_dav_destination_traversal_blocked(auth):
    """Destination 清洗(_dav_destination)不滤 '..' —— 防线在 resolve 的
    safe_path(拒点前缀段)。此用例把这条纵深钉在 API 层:清洗将来改动
    (比如放开更多字符)时,resolve 的 backstop 一旦破坏立刻红。"""
    old = f"davtrav{_rand()}.txt"
    assert auth.put(f"/dav/private/admin/{old}", content=b"v").status_code in (200, 201, 204)
    # 目标带 .. 试图逃出空间:必须 4xx,且旧文件仍原位
    r = auth.request("MOVE", f"/dav/private/admin/{old}",
                     headers={"Destination": "https://testserver/dav/private/admin/../../escape.txt"})
    assert r.status_code in (400, 403), r.text
    assert auth.get(f"/dav/private/admin/{old}").status_code == 200


# ── PUT 覆盖 × 配额预检次序（2026-11-08 值守批缺陷钉）─────────────────


def _mk_member_with_quota(auth, client, quota: int) -> tuple[str, object]:
    """建 member + 设配额，返回 (username, member_session)。"""
    from test_extras import _mk_member, _member_session
    from app.db import _db
    uname = f"davq{secrets.token_hex(3)}"
    _mk_member(client, uname)
    member = _member_session(client, uname)
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                        (uname,)).fetchone()["id"]
    r = auth.patch(f"/api/admin/users/{uid}", json={"quota_bytes": quota})
    assert r.status_code == 200, r.text
    return uname, member


def test_dav_put_overwrite_quota_fail_keeps_old_file(client, auth):
    """配额预检失败不得动旧版。

    原缺陷：PUT 覆盖先 move_to_trash 旧版再验配额 —— 验挂（409）时旧
    文件已离开原位，失败的替换操作破坏既有状态（今天生产无人设配额，
    属潜伏缺陷，仍按「失败必须零副作用」修）。修复后预检先于一切写动作，
    计价 incoming=-old_size（净增量）。回退修复 → GET 原路径 404（旧文件
    已进回收站）→ 本钉红。
    """
    import secrets as _secrets
    uname, member = _mk_member_with_quota(auth, client, 1000)
    name = f"davq{_rand()}.txt"
    path = f"/dav/private/{uname}/{name}"
    r = member.put(path, content=b"a" * 600)          # 首传 600B，占 600/1000
    assert r.status_code in (200, 201, 204), r.text
    # 覆盖为 1200B：落位后 1200 > 1000 → 409；旧文件必须原位原样
    r = member.put(path, content=b"b" * 1200)
    assert r.status_code == 409, r.text
    g = member.get(path)
    assert g.status_code == 200, f"旧文件必须还在原位: {g.status_code}"
    assert g.content == b"a" * 600, "旧文件内容不得被换掉"
    # 目录里不得留 .dav-uploading-* 半截 tmp
    from app.config import SHARE_ROOT
    residue = [p.name for p in (SHARE_ROOT / "private" / uname).iterdir()
               if p.name.startswith(".dav-")]
    assert residue == [], residue


def test_dav_put_overwrite_within_quota_succeeds(client, auth):
    """配额内覆盖照常成功且内容替换（修复不得误伤 happy path）。"""
    uname, member = _mk_member_with_quota(auth, client, 2000)
    name = f"davq{_rand()}.txt"
    path = f"/dav/private/{uname}/{name}"
    assert member.put(path, content=b"a" * 600).status_code in (200, 201, 204)
    r = member.put(path, content=b"b" * 900)          # 落位后 900 ≤ 2000
    assert r.status_code in (200, 204), r.text
    assert member.get(path).content == b"b" * 900
    # 旧版进了回收站（与界面覆盖语义一致）
    from app.config import SHARE_ROOT
    trash_has_old = any(
        "davq" in p.name for p in (SHARE_ROOT / ".trash").rglob("*") if p.is_file())
    assert trash_has_old


def test_dav_put_create_over_quota_no_residue(client, auth):
    """创建超配额 → 409 + 零 tmp 残留（同路径的创建分支也钉住）。"""
    uname, member = _mk_member_with_quota(auth, client, 100)
    name = f"davq{_rand()}.txt"
    r = member.put(f"/dav/private/{uname}/{name}", content=b"x" * 500)
    assert r.status_code == 409, r.text
    from app.config import SHARE_ROOT
    residue = [p.name for p in (SHARE_ROOT / "private" / uname).iterdir()
               if p.name.startswith(".dav-")]
    assert residue == [], residue
