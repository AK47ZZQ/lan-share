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
