"""对抗性测试（2026-10-31 Round 2）。

不是功能验证，是想办法把它弄坏：
  * 鉴权：各种畸形 token 能不能蒙混过关
  * 路径穿越：能不能把文件写到收件箱外面去
  * 输入污染：能不能让生成的 Caddyfile / 注册表脚本出问题
  * 边界：空、超长、Unicode、控制字符、并发

每个用例都写清「如果这里失守会怎样」，方便以后判断哪些改动是危险的。
"""
import os
import threading

import pytest

from app import settings
from app.config import SHARE_ROOT
from lanshare_cli import caddyfile

# ────────────────────────────────────────────────────────────
# 一、收件箱鉴权：想办法绕过 token
# ────────────────────────────────────────────────────────────


def _tok(v: str) -> None:
    settings.set_setting(settings.FEEDBACK_INBOX_TOKEN, v)


@pytest.fixture()
def inbox_on(client):
    t = "secret-" + os.urandom(4).hex()
    _tok(t)
    return t


class TestInboxAuthBypass:
    def test_empty_authorization_header(self, client, inbox_on):
        r = client.post("/api/feedback/inbox", json={"id": "a", "title": "x"},
                        headers={"Authorization": ""})
        assert r.status_code == 403

    def test_bearer_prefix_only(self, client, inbox_on):
        """`Bearer ` 后面什么都没有 —— 不能让 strip 后变空还通过。"""
        r = client.post("/api/feedback/inbox", json={"id": "b", "title": "x"},
                        headers={"Authorization": "Bearer "})
        assert r.status_code == 403

    def test_prefix_of_real_token(self, client, inbox_on):
        """提交真实 token 的前缀。时序攻击的常见利用点，必须 403。"""
        r = client.post("/api/feedback/inbox", json={"id": "c", "title": "x"},
                        headers={"Authorization": "Bearer " + inbox_on[:-1]})
        assert r.status_code == 403

    def test_token_with_extra_suffix(self, client, inbox_on):
        r = client.post("/api/feedback/inbox", json={"id": "d", "title": "x"},
                        headers={"Authorization": "Bearer " + inbox_on + "x"})
        assert r.status_code == 403

    def test_raw_token_without_bearer(self, client, inbox_on):
        """直接把 token 塞进 Authorization，不带 Bearer 前缀。"""
        r = client.post("/api/feedback/inbox", json={"id": "e", "title": "x"},
                        headers={"Authorization": inbox_on})
        assert r.status_code == 403

    def test_basic_scheme_not_accepted(self, client, inbox_on):
        """Basic 认证里塞 token —— 只认 Bearer。"""
        import base64
        cred = base64.b64encode(f":{inbox_on}".encode()).decode()
        r = client.post("/api/feedback/inbox", json={"id": "f", "title": "x"},
                        headers={"Authorization": f"Basic {cred}"})
        assert r.status_code == 403

    def test_ascii_lookalike_token(self, client, inbox_on):
        """ASCII 范围内的相似字符冒充（- 换 _，0 换 O，l 换 1）。

        注：全角字符那种用例不成立 —— HTTP header 只允许 ASCII，
        httpx 在客户端就直接拒了，根本到不了服务端。
        """
        fake = inbox_on.replace("-", "_").replace("0", "O").replace("l", "1")
        if fake == inbox_on:
            pytest.skip("这个 token 没有可替换的相似字符")
        r = client.post("/api/feedback/inbox", json={"id": "g", "title": "x"},
                        headers={"Authorization": "Bearer " + fake})
        assert r.status_code == 403


# ────────────────────────────────────────────────────────────
# 二、路径穿越：把文件写到收件箱之外
# ────────────────────────────────────────────────────────────


class TestInboxPathTraversal:
    @pytest.mark.parametrize("evil_source", [
        "../../etc",                       # 相对穿越
        "..\\..\\Windows",                 # Windows 相对穿越
        "C:\\Windows\\System32",           # 绝对路径
        "\\\\server\\share",               # UNC
        "a/../../../b",                    # 夹在中间
        "..",                              # 只有两个点
        "./.",                             # 当前目录
        "",                                # 空
        "   ",                             # 全空格
        "a" * 200,                         # 超长
    ])
    def test_evil_source_cannot_escape(self, client, inbox_on, evil_source):
        """来源目录名是唯一由外部控制的路径片段，不能穿出去。"""
        inbox = SHARE_ROOT / "反馈" / "收件箱"
        before = set(inbox.rglob("*")) if inbox.exists() else set()

        r = client.post(
            "/api/feedback/inbox",
            json={"id": "trav" + os.urandom(2).hex(), "title": "x",
                  "source": evil_source},
            headers={"Authorization": f"Bearer {inbox_on}"},
        )
        # 要么接受（清洗后落盘），要么拒绝；关键是不能写到外面
        assert r.status_code in (200, 403, 422)

        after = set(inbox.rglob("*")) if inbox.exists() else set()
        for p in after - before:
            if p.is_file():
                # 落点必须在收件箱内
                assert str(p.resolve()).startswith(str(inbox.resolve())), \
                    f"文件跑出收件箱了: {p}"

    @pytest.mark.parametrize("evil_id", [
        "../../evil", "..\\evil", "C:/Windows/evil",
        "....//....//evil", "a\x00b", "a\nb",
    ])
    def test_evil_id_cannot_escape(self, client, inbox_on, evil_id):
        """编号同样参与文件名，也不能带出路径。"""
        inbox = SHARE_ROOT / "反馈" / "收件箱"
        r = client.post(
            "/api/feedback/inbox",
            json={"id": evil_id, "title": "x", "source": "idt"},
            headers={"Authorization": f"Bearer {inbox_on}"},
        )
        assert r.status_code in (200, 403, 422)
        # 不能出现 SHARE_ROOT/evil.md 这种
        assert not (SHARE_ROOT / "evil.md").exists()


# ────────────────────────────────────────────────────────────
# 三、输入污染：Caddyfile 生成
# ────────────────────────────────────────────────────────────


class TestCaddyfileInjection:
    @pytest.mark.parametrize("evil_host", [
        "localhost\ntls off",       # 换行另起一条指令
        "192.168.1.1 {}",          # 花括号提前闭合站点块
        "192.168.1.1; tls off",    # 分号
        "",                        # 空
        "   ",                     # 全空格
        "a" * 300,                 # 超长
    ])
    def test_evil_host_rejected(self, evil_host):
        """主机名进了站点地址行，必须白名单校验。

        含换行能另起一条指令，含花括号能提前闭合站点块 —— 都是实打实的
        配置注入。合法主机名只有字母数字点连字符。
        """
        with pytest.raises(ValueError, match="主机名"):
            caddyfile.render(api_port=8000, web_port=8001, hosts=[evil_host])

    def test_host_is_trimmed_not_rejected(self):
        """首尾空格是合理的宽容，strip 后合法就接受。"""
        text = caddyfile.render(
            api_port=8000, web_port=8001, hosts=["  192.168.1.12  "]
        )
        assert "https://192.168.1.12:8001" in text

    def test_normal_hosts_still_work(self):
        """校验不能误伤正常主机名。"""
        text = caddyfile.render(
            api_port=8000, web_port=8001,
            hosts=["localhost", "192.168.1.12", "my-nas.local"],
        )
        assert "https://192.168.1.12:8001" in text
        assert "https://my-nas.local:8001" in text

    def test_port_must_be_valid(self):
        for bad in (0, -1, 65536, 10**9):
            with pytest.raises(ValueError):
                caddyfile.render(api_port=bad, web_port=9000)
            with pytest.raises(ValueError):
                caddyfile.render(api_port=9000, web_port=bad)

    def test_same_port_rejected(self):
        with pytest.raises(ValueError, match="不能相同"):
            caddyfile.render(api_port=9000, web_port=9000)


# ────────────────────────────────────────────────────────────
# 四、注册表键名注入
# ────────────────────────────────────────────────────────────


class TestRegistryKeyInjection:
    @pytest.mark.parametrize("evil", [
        "LANShare'; Remove-Item C:\\ -Recurse; '",
        "LANShare\"; rm -rf /",
        "LANShare; shutdown /s",
        "LANShare\nRemove-Item",
        "",
    ])
    def test_registry_key_rejects_injection(self, evil):
        """注册表键名会拼进 PowerShell 脚本，必须白名单校验。"""
        from lanshare_cli.cli import write_registry_value

        with pytest.raises((ValueError, Exception)):
            write_registry_value("Test", "x", evil)

    @pytest.mark.parametrize("evil", [
        "LANShare`; calc",
        "LANShare$(calc)",
        "LANShare' ; Stop-Service lan-share-api; '",
    ])
    def test_registry_key_rejects_ps_expansion(self, evil):
        """PowerShell 的反引号/子表达式展开。"""
        from lanshare_cli.cli import write_registry_value

        with pytest.raises((ValueError, Exception)):
            write_registry_value("Test", "x", evil)


# ────────────────────────────────────────────────────────────
# 五、并发
# ────────────────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_inbox_writes_same_id(self, client, inbox_on):
        """同一个编号并发提交 —— 不能写坏文件、不能 500。"""
        fid = "conc" + os.urandom(3).hex()
        results = []

        def fire():
            r = client.post(
                "/api/feedback/inbox",
                json={"id": fid, "title": "并发", "source": "conctest"},
                headers={"Authorization": f"Bearer {inbox_on}"},
            )
            results.append(r.status_code)

        threads = [threading.Thread(target=fire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全部 200（重复的返回 dup），不能有 500
        assert all(c == 200 for c in results), results

        path = SHARE_ROOT / "反馈" / "收件箱" / "conctest" / f"{fid}.md"
        assert path.is_file()
        # 文件内容完整，不是两个线程写一半的残骸
        text = path.read_text(encoding="utf-8")
        assert "并发" in text
        assert text.count("---") >= 2
