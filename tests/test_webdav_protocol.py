"""WebDAV 协议最小集回归（B10 补覆盖：此前全库仅 1 条 PROPFIND 冒烟）。

覆盖五方法主链路：PUT → GET 内容一致、MKCOL → PROPFIND 列出、MOVE 重命名
（Destination 头 + 旧路径消失）、DELETE（进服务端回收站）、OPTIONS 能力头；
外加 Basic 鉴权拒绝与成员越权写他人 private 的 403（越权铁律在 DAV 侧的
同款收口）。客户端全部走 auth fixture（Cookie 会话过 _dav_auth）。
"""
import base64

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
