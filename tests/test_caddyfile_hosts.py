"""Caddyfile 生成的站点地址必须覆盖托盘和 CLI 实际用的主机名。

这个文件是 2026-11-01 加的，源自一次真机事故：干净机器装 3.80，
服务三个都 Running、API 直连 200，可托盘界面一片红、「启动局域网服务」
按钮点了没反应、地址不显示、二维码也没有。

根因：证书签给了 localhost 和 LAN IP，而托盘探活用的是 127.0.0.1，
不在 SAN 里 → TLS 握手 SEC_E_ILLEGAL_MESSAGE → 托盘永远判定「服务未运行」。

这类 bug 的可怕之处是「每层单看都对」：服务真的在跑、Caddy 真的在监听、
日志里全是 info 没有 error。只有把「探针用的地址」和「证书签给的地址」
两个列表摆在一起才看得出来。
"""

from __future__ import annotations

from lanshare_cli import caddyfile


class TestDefaultHosts:
    def test_always_includes_localhost(self):
        hosts = caddyfile.default_hosts()
        assert "localhost" in hosts

    def test_always_includes_loopback_ip(self):
        """127.0.0.1 必须在 SAN 里。

        托盘的健康探测、「打开网盘」、CLI 自检探针都用它。
        不在 SAN 里的话，服务活着但所有探针都 TLS 失败。
        """
        hosts = caddyfile.default_hosts()
        assert "127.0.0.1" in hosts, (
            "127.0.0.1 必须在证书 SAN 里，否则托盘探活永远 SecureChannelFailure"
        )

    def test_no_duplicates(self):
        hosts = caddyfile.default_hosts()
        assert len(hosts) == len(set(hosts))

    def test_survives_network_probe_failure(self, monkeypatch):
        """网卡探测炸了也要返回可用结果，不能阻断安装。"""

        def boom():
            raise OSError("no network")

        monkeypatch.setattr("lanshare_cli.network.list_lan_addresses", boom)
        hosts = caddyfile.default_hosts()
        assert "localhost" in hosts
        assert "127.0.0.1" in hosts


class TestRenderCoversProbeHosts:
    """生成的 Caddyfile 里站点地址必须包含每个探针主机名。"""

    def test_render_includes_all_default_hosts(self):
        hosts = caddyfile.default_hosts()
        text = caddyfile.render(api_port=8766, web_port=8765, hosts=hosts)
        for h in hosts:
            assert h in text, f"站点地址缺 {h}，证书就不会含它"

    def test_explicit_hosts_respected(self):
        text = caddyfile.render(api_port=9911, web_port=9900,
                                hosts=["localhost", "127.0.0.1"])
        assert "localhost" in text
        assert "127.0.0.1" in text
        assert "9900" in text


class TestRenderPaths:
    """路径参数（含空格 / 反斜杠）必须安全落进 Caddyfile。

    安装目录含空格是常态（Windows 默认 Program Files、用户自选
    "LAN Share"），Caddyfile 按空格切 token —— 模板里已经加了一对引号，
    这里锁住「反斜杠必须转正斜杠 + 引号必须还在」这条底线。
    """

    ROOT = "C:\\Program Files\\LAN Share"

    def test_paths_with_spaces_and_backslashes(self):
        text = caddyfile.render(
            api_port=8766, web_port=8765, hosts=["localhost", "127.0.0.1"],
            storage_dir=self.ROOT + "\\certs\\caddy",
            web_root=self.ROOT + "\\web\\dist",
            updates_root=self.ROOT + "\\updates",
        )
        # 反斜杠全部转正斜杠（Caddy 对 Windows 反斜杠不稳妥）
        assert "\\" not in text
        # 模板的引号必须包住带空格的路径，不能被吃掉
        assert 'root * "C:/Program Files/LAN Share/web/dist"' in text
        assert 'root * "C:/Program Files/LAN Share/updates"' in text
        assert 'storage file_system "C:/Program Files/LAN Share/certs/caddy"' in text

    def test_updates_block_present_with_handle_path(self):
        """更新目录必须用 handle_path（剥前缀）而不是 handle（不剥 → 404）。"""
        text = caddyfile.render(
            api_port=8766, web_port=8765, hosts=["localhost"],
            updates_root="C:/Program Files/LAN Share/updates",
        )
        assert "handle_path /updates/*" in text
        assert 'root * "C:/Program Files/LAN Share/updates"' in text

    def test_static_blocks_present(self):
        """/assets /static /icons 三块静态托管是「assets 403 白屏」事故的修复。"""
        text = caddyfile.render(api_port=8766, web_port=8765, hosts=["localhost"])
        for prefix in ("/assets/*", "/static/*", "/icons/*"):
            assert f"handle {prefix}" in text


class TestExistingDomainsPreserved:
    """升级 / 重装 / 切局域网开关，都不能把公网域名弄丢。

    2026-11-04 实机：4.0.4 → 4.0.5 一键更新完成后，公网立刻打不开了。
    根因是安装器升级时会跑 `install --force`，它用 default_hosts() 整份重写
    Caddyfile —— 那个函数只认本机网卡地址，用户配好的穿透域名不在里面，
    于是域名从站点地址里消失，证书 SAN 不再包含它，公网 TLS 直接失败。

    这类 bug 特别难查：症状（公网打不开）和操作（点了个更新）看起来毫无关系。
    所以修法不是「在 install 里补一句」，而是让 write() 永远保住旧域名 ——
    重写 Caddyfile 的地方有三处，逐个补必然漏。
    """

    def _make(self, tmp_path, body: str):
        f = tmp_path / "Caddyfile"
        f.write_text(body, encoding="utf-8", newline="\n")
        return f

    def test_extracts_domain(self, tmp_path):
        f = self._make(tmp_path,
                       "https://localhost:8765, https://my-frp.example.com:8765 {\n}\n")
        assert caddyfile.existing_domains(f) == ["my-frp.example.com"]

    def test_extracts_multiple_and_dedupes(self, tmp_path):
        f = self._make(tmp_path,
                       "https://a.example.com:8765, https://b.example.com:8767, "
                       "https://a.example.com:8767 {\n}\n")
        assert caddyfile.existing_domains(f) == ["a.example.com", "b.example.com"]

    def test_skips_ip_and_localhost(self, tmp_path):
        f = self._make(tmp_path,
                       "https://localhost:8765, https://127.0.0.1:8765, "
                       "https://192.168.1.100:8765, https://[::1]:8765 {\n}\n")
        assert caddyfile.existing_domains(f) == []

    def test_skips_garbage(self, tmp_path):
        """手改过的文件里什么都可能有，不能照单全收塞进证书 SAN。"""
        f = self._make(tmp_path,
                       "https://bad_host!:8765, https://...:8765, https://:8765 {\n}\n")
        assert caddyfile.existing_domains(f) == []

    def test_bare_hostname_without_scheme(self, tmp_path):
        """Caddy 官方文档最标准的写法就是裸主机名，只认 https:// 会漏。"""
        f = self._make(tmp_path, "my-frp.example.com:8765 {\n}\n")
        assert caddyfile.existing_domains(f) == ["my-frp.example.com"]

    def test_ignores_other_sites_ports(self, tmp_path):
        """别的站点块（别的端口）的主机名不能被吸收进网盘站点。

        否则 Caddy 会给它签证书、把本该 respond 的路径变成反代到网盘 ——
        凭空多一个入口。审查实测能复现。
        """
        f = self._make(tmp_path,
                       "https://localhost:8765, https://my-frp.example.com:8765 {\n}\n"
                       "https://admin.internal:9000 {\n\trespond \"no\"\n}\n")
        assert caddyfile.existing_domains(f, ports=[8765, 8767]) == ["my-frp.example.com"]

    def test_strips_trailing_comment(self, tmp_path):
        f = self._make(tmp_path, "https://my-frp.example.com:8765 {  # 公网入口\n}\n")
        assert caddyfile.existing_domains(f) == ["my-frp.example.com"]

    def test_tunnel_port_only_when_in_use(self, tmp_path):
        """没在用隧道的机器不该开 8767 —— 端口被占时 Caddy 整份配置起不来。"""
        f = self._make(tmp_path, "https://localhost:8765 {\n}\n")
        caddyfile.write(f, api_port=8766, web_port=8765)
        text = f.read_text(encoding="utf-8")
        assert ":8767" not in text, "没有隧道的机器不该监听隧道端口"

    def test_tunnel_port_kept_when_domain_present(self, tmp_path):
        """已经在用隧道的机器：升级后 8767 必须还在，否则隧道连不上。"""
        f = self._make(tmp_path,
                       "https://localhost:8765, https://my-frp.example.com:8765, "
                       "https://my-frp.example.com:8767 {\n}\n")
        caddyfile.write(f, api_port=8766, web_port=8765)
        text = f.read_text(encoding="utf-8")
        assert "my-frp.example.com:8767" in text

    def test_missing_file(self, tmp_path):
        assert caddyfile.existing_domains(tmp_path / "nope") == []

    def test_write_keeps_existing_domain(self, tmp_path):
        f = self._make(tmp_path,
                       "https://localhost:8765, https://my-frp.example.com:8765 {\n"
                       "\tbind 0.0.0.0\n}\n")
        caddyfile.write(f, api_port=8766, web_port=8765)

        text = f.read_text(encoding="utf-8")
        site = [ln for ln in text.splitlines() if ln.startswith("https://")][0]
        assert "my-frp.example.com" in site, "重写把公网域名弄丢了——公网会静默失效"

    def test_write_no_duplicate_when_hosts_passed(self, tmp_path):
        """调用方已经带了这个域名时不要写两遍（Caddy 会报重复站点）。"""
        f = self._make(tmp_path, "https://localhost:8765, https://my-frp.example.com:8765 {\n}\n")
        caddyfile.write(f, api_port=8766, web_port=8765,
                        hosts=["localhost", "my-frp.example.com"])
        site = [ln for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.startswith("https://")][0]
        # 域名在 8765（主入口）和 8767（隧道端口）各出现一次，这是设计；
        # 「重复」指的是同一个 host:port 写了两遍。
        assert site.count("https://my-frp.example.com:8765") == 1
        assert site.count("https://my-frp.example.com:8767") == 1

    def test_replace_mode_drops_old_domain(self, tmp_path):
        """一键接入换了域名：旧的必须被替换掉，不能越攒越多。

        keep_existing_domains=False 就是给这个场景的。没有它，用户在 Caddyfile
        里手动删掉域名都没用 —— write() 下次又会读回来加进去，等于「只增不减」。
        """
        f = self._make(tmp_path,
                       "https://localhost:8765, https://old-frp.example.com:8765 {\n}\n")
        caddyfile.write(f, api_port=8766, web_port=8765,
                        hosts=["localhost", "new-frp.example.com"],
                        keep_existing_domains=False)
        site = [ln for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.startswith("https://")][0]
        assert "new-frp.example.com" in site
        assert "old-frp.example.com" not in site

    def test_write_without_old_file_uses_defaults(self, tmp_path):
        f = tmp_path / "Caddyfile"
        caddyfile.write(f, api_port=8766, web_port=8765)
        site = [ln for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.startswith("https://")][0]
        assert "localhost" in site
