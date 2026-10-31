"""局域网开关（lan）的测试。

三个重点：
    1. 锁死 2026-10-31 实测踩到的坑（NSSM PathName、HTTPS 400、-AsArray）
    2. 状态机的四态判断
    3. 「开关 = 改监听地址」这个设计约束 —— 关闭时绝不能碰防火墙

外部依赖（网络探测、防火墙、服务）全部 monkeypatch 掉，
测试不碰真实防火墙、不需要管理员权限、不依赖本机网络。
"""

from __future__ import annotations

import pytest

from lanshare_cli import caddyfile, firewall, lan, network


# ──────────────────────────────────────────────────────────────
# caddyfile：监听地址是开关的唯一实现机制
# ──────────────────────────────────────────────────────────────


class TestSiteBlock:
    def test_bind_all_renders_bare_port(self):
        """绑所有接口写成 `:8765`，不能写 `0.0.0.0:8765`。

        Caddy 会把 0.0.0.0 当主机名处理，行为不符合预期。
        """
        assert caddyfile.site_block(caddyfile.BIND_ALL, 8765) == ":8765"

    def test_bind_loopback_renders_explicit_host(self):
        assert caddyfile.site_block(caddyfile.BIND_LOOPBACK, 8765) == "127.0.0.1:8765"


class TestRender:
    def test_default_is_all_interfaces(self):
        """默认（不传 bind_host）必须和改造前一致，不能悄悄改变现有部署行为。"""
        content = caddyfile.render(api_port=8766, web_port=8765)
        assert ":8765 {" in content

    def test_loopback_render(self):
        content = caddyfile.render(8766, 8765, caddyfile.BIND_LOOPBACK)
        assert "127.0.0.1:8765 {" in content
        assert ":8765 {" not in content.replace("127.0.0.1:8765 {", "")

    def test_reverse_proxy_target_unaffected_by_bind(self):
        """绑哪里不影响反代目标 —— 始终指回环上的 API。"""
        for host in (caddyfile.BIND_ALL, caddyfile.BIND_LOOPBACK):
            content = caddyfile.render(8766, 8765, host)
            assert "reverse_proxy 127.0.0.1:8766" in content

    def test_rejects_same_ports(self):
        with pytest.raises(ValueError):
            caddyfile.render(8765, 8765)


class TestDetectBindHost:
    """重装时还原用户选择 —— 这是第二批的基础。"""

    def test_detects_all(self, tmp_path):
        target = tmp_path / "Caddyfile"
        target.write_text(caddyfile.render(8766, 8765, caddyfile.BIND_ALL), encoding="utf-8")
        assert caddyfile.detect_bind_host(target) == caddyfile.BIND_ALL

    def test_detects_loopback(self, tmp_path):
        target = tmp_path / "Caddyfile"
        target.write_text(
            caddyfile.render(8766, 8765, caddyfile.BIND_LOOPBACK), encoding="utf-8"
        )
        assert caddyfile.detect_bind_host(target) == caddyfile.BIND_LOOPBACK

    def test_missing_file_returns_none(self, tmp_path):
        assert caddyfile.detect_bind_host(tmp_path / "nope") is None

    def test_garbage_file_returns_none(self, tmp_path):
        target = tmp_path / "Caddyfile"
        target.write_text("这不是 Caddyfile\n", encoding="utf-8")
        assert caddyfile.detect_bind_host(target) is None

    def test_roundtrip(self, tmp_path):
        """写进去什么，读出来就是什么 —— 保证 install 还原可靠。"""
        target = tmp_path / "Caddyfile"
        for host in (caddyfile.BIND_ALL, caddyfile.BIND_LOOPBACK):
            caddyfile.write(target, 8766, 8765, host)
            assert caddyfile.detect_bind_host(target) == host


# ──────────────────────────────────────────────────────────────
# 防火墙：路径解析（NSSM 坑）
# ──────────────────────────────────────────────────────────────


class TestParseImagePath:
    def test_quoted_with_args(self):
        raw = '"C:\\Program Files\\LAN Share\\bin\\caddy.exe" run --config conf\\Caddyfile'
        assert firewall.parse_image_path(raw) == "C:\\Program Files\\LAN Share\\bin\\caddy.exe"

    def test_unquoted_no_space(self):
        raw = "C:\\LANShare\\bin\\caddy.exe run --config Caddyfile"
        assert firewall.parse_image_path(raw) == "C:\\LANShare\\bin\\caddy.exe"

    def test_bare_exe(self):
        raw = "C:\\lan-share\\bin\\caddy.exe"
        assert firewall.parse_image_path(raw) == "C:\\lan-share\\bin\\caddy.exe"

    def test_empty_returns_empty(self):
        assert firewall.parse_image_path("") == ""

    def test_exe_uppercase_still_found(self):
        """Windows 路径大小写不敏感，.EXE 也要能认。"""
        raw = "C:\\Foo\\CADDY.EXE run"
        assert firewall.parse_image_path(raw).lower().endswith("caddy.exe")


class TestRuleMatches:
    """规则形态校验 —— 决定是否需要重建。"""

    def _rule(self, **overrides):
        defaults = dict(
            display_name="LAN Share (TCP 8765)",
            enabled=True,
            action="Allow",
            profiles=["Private", "Domain"],
            local_ports=["8765"],
            program="C:\\lan-share\\bin\\caddy.exe",
        )
        defaults.update(overrides)
        return firewall.FirewallRule(**defaults)

    def test_correct_rule_matches(self):
        rule = self._rule()
        assert rule.matches(8765, "C:\\lan-share\\bin\\caddy.exe")

    def test_public_profile_never_matches(self):
        """含 Public 的规则一律判不合格 —— 安全底线。

        公用网络（咖啡厅 WiFi）下放行整个网盘是绝对不能接受的状态，
        哪怕端口和程序都对，也要重建。
        """
        rule = self._rule(profiles=["Private", "Public"])
        assert not rule.matches(8765, "C:\\lan-share\\bin\\caddy.exe")

    def test_disabled_rule_matches_nothing(self):
        rule = self._rule(enabled=False)
        assert not rule.matches(8765, "C:\\lan-share\\bin\\caddy.exe")

    def test_wrong_program_mismatches(self):
        """程序搬家后旧规则必须判不合格，触发重建。"""
        rule = self._rule(program="C:\\Old\\caddy.exe")
        assert not rule.matches(8765, "C:\\New\\caddy.exe")

    def test_wrong_port_mismatches(self):
        rule = self._rule(local_ports=["9999"])
        assert not rule.matches(8765, "C:\\lan-share\\bin\\caddy.exe")

    def test_path_compare_ignores_case_and_slash_style(self):
        """Windows 路径大小写不敏感，正反斜杠也可能混。"""
        rule = self._rule(program="c:/LAN-SHARE/bin/CADDY.EXE")
        assert rule.matches(8765, "C:\\lan-share\\bin\\caddy.exe")

    def test_rule_state_missing(self, monkeypatch):
        monkeypatch.setattr(firewall, "find_rules", lambda port=None: [])
        assert firewall.rule_state(8765, "C:\\x\\caddy.exe") == "missing"

    def test_rule_state_stale(self, monkeypatch):
        stale = self._rule(profiles=["Public"])
        monkeypatch.setattr(firewall, "find_rules", lambda port=None: [stale])
        assert firewall.rule_state(8765, "C:\\lan-share\\bin\\caddy.exe") == "stale"

    def test_rule_state_ok(self, monkeypatch):
        good = self._rule()
        monkeypatch.setattr(firewall, "find_rules", lambda port=None: [good])
        assert firewall.rule_state(8765, "C:\\lan-share\\bin\\caddy.exe") == "ok"


# ──────────────────────────────────────────────────────────────
# network：地址过滤
# ──────────────────────────────────────────────────────────────


class TestLanAddressFiltering:
    """虚拟网卡和 APIPA 必须被排除，否则会展示不可用的地址。"""

    def test_apipa_is_not_private(self):
        addr = network.LanAddress(
            ip="169.254.115.140", alias="以太网", prefix_length=16,
            has_gateway=False, metric=99999,
        )
        assert not addr.is_private

    def test_real_lan_is_private(self):
        addr = network.LanAddress(
            ip="192.168.1.12", alias="以太网", prefix_length=24,
            has_gateway=True, metric=1,
        )
        assert addr.is_private

    def test_public_ip_detected(self):
        """公网 IP 要能被识别出来 —— 这种情况下开启等于对全网暴露。"""
        addr = network.LanAddress(
            ip="8.8.8.8", alias="以太网", prefix_length=24,
            has_gateway=True, metric=1,
        )
        assert not addr.is_private

    def test_virtual_hints_cover_common_cards(self):
        for hint in ("vmware", "vethernet", "tailscale", "virtualbox", "wsl"):
            assert hint in network._VIRTUAL_HINTS


class TestCandidateUrls:
    """协议自适应 —— 2026-10-31 踩过的坑：老部署 HTTPS，新安装包 HTTP。"""

    def test_both_tried_by_default(self):
        urls = lan.candidate_urls("192.168.1.12", 8765)
        assert any(u.startswith("http://") for u in urls)
        assert any(u.startswith("https://") for u in urls)

    def test_http_only_when_forced(self):
        urls = lan.candidate_urls("192.168.1.12", 8765, use_https=False)
        assert urls == ["http://192.168.1.12:8765/healthz"]

    def test_https_only_when_forced(self):
        urls = lan.candidate_urls("192.168.1.12", 8765, use_https=True)
        assert urls == ["https://192.168.1.12:8765/healthz"]


# ──────────────────────────────────────────────────────────────
# lan：状态机
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def info(tmp_path):
    return lan.InstallInfo(
        root=tmp_path,
        data_dir=tmp_path / "data",
        api_port=8766,
        web_port=8765,
        service_prefix="test",
    )


class TestCollectStatus:
    def test_no_service_is_uninstalled(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda name: "")
        status = lan.collect_status(info)
        assert status.state == lan.LanState.UNINSTALLED

    def test_no_listener_is_uninstalled(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "listen_addresses", lambda p: [])
        status = lan.collect_status(info)
        assert status.state == lan.LanState.UNINSTALLED

    def test_loopback_only_is_off(self, info, monkeypatch):
        """只绑回环 = 未开启，且不需要做自检。"""
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "listen_addresses", lambda p: ["127.0.0.1"])
        monkeypatch.setattr(network, "listens_on_loopback_only", lambda p: True)
        monkeypatch.setattr(network, "listens_on_all_interfaces", lambda p: False)
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("192.168.1.12", "以太网", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("home", "以太网", "Private")
        ])
        monkeypatch.setattr(firewall, "rule_state", lambda p, e: "ok")

        status = lan.collect_status(info)
        assert status.state == lan.LanState.OFF
        assert status.probe_url == ""       # 未开启时不该自检

    def test_all_interfaces_plus_probe_ok_is_on(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "listen_addresses", lambda p: ["0.0.0.0"])
        monkeypatch.setattr(network, "listens_on_loopback_only", lambda p: False)
        monkeypatch.setattr(network, "listens_on_all_interfaces", lambda p: True)
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("192.168.1.12", "以太网", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("home", "以太网", "Private")
        ])
        monkeypatch.setattr(firewall, "rule_state", lambda p, e: "ok")
        monkeypatch.setattr(lan, "probe_lan", lambda *a, **k: (True, "ok", "http://192.168.1.12:8765/healthz"))

        status = lan.collect_status(info)
        assert status.state == lan.LanState.ON
        assert status.probe_ok

    def test_probe_failure_is_blocked_not_on(self, info, monkeypatch):
        """最关键的一条：绑了所有接口但连不上，必须是 BLOCKED 而不是 ON。

        混在 ON 里等于告诉用户「成了」，结果手机连不上。
        """
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "listen_addresses", lambda p: ["0.0.0.0"])
        monkeypatch.setattr(network, "listens_on_loopback_only", lambda p: False)
        monkeypatch.setattr(network, "listens_on_all_interfaces", lambda p: True)
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("192.168.1.12", "以太网", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("home", "以太网", "Private")
        ])
        monkeypatch.setattr(firewall, "rule_state", lambda p, e: "ok")
        monkeypatch.setattr(lan, "probe_lan", lambda *a, **k: (False, "超时", "http://x/healthz"))

        status = lan.collect_status(info)
        assert status.state == lan.LanState.BLOCKED
        assert status.hints                      # 必须有排查提示

    def test_no_network_addresses(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "listen_addresses", lambda p: ["0.0.0.0"])
        monkeypatch.setattr(network, "listens_on_loopback_only", lambda p: False)
        monkeypatch.setattr(network, "listens_on_all_interfaces", lambda p: True)
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [])
        monkeypatch.setattr(firewall, "rule_state", lambda p, e: "ok")

        status = lan.collect_status(info)
        assert status.state == lan.LanState.NO_NETWORK

    def test_public_network_hint_mentions_public(self, info, monkeypatch):
        """公用网络拿不到访问时，提示里必须点名「公用」。"""
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "listen_addresses", lambda p: ["0.0.0.0"])
        monkeypatch.setattr(network, "listens_on_loopback_only", lambda p: False)
        monkeypatch.setattr(network, "listens_on_all_interfaces", lambda p: True)
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("10.0.0.5", "WiFi", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("CoffeeShop", "WiFi", "Public")
        ])
        monkeypatch.setattr(firewall, "rule_state", lambda p, e: "ok")
        monkeypatch.setattr(lan, "probe_lan", lambda *a, **k: (False, "超时", "http://x/healthz"))

        status = lan.collect_status(info)
        assert status.state == lan.LanState.BLOCKED
        assert any("公用" in h for h in status.hints)


# ──────────────────────────────────────────────────────────────
# lan：开关动作
# ──────────────────────────────────────────────────────────────


class TestEnableRejectsPublicOnly:
    """只有公用网络时必须停下来 —— 规则对公用网络不生效，硬开等于假装成功。"""

    def test_public_only_rejected_without_flag(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("10.0.0.5", "WiFi", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("Cafe", "WiFi", "Public")
        ])

        report = lan.enable_lan(info)
        assert not report.success
        assert any("公用" in detail for _, _, detail in report.steps)

    def test_public_only_allowed_with_flag(self, info, monkeypatch):
        """用户明确确认后可以继续（allow_public=True）。"""
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("10.0.0.5", "WiFi", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("Cafe", "WiFi", "Public")
        ])
        monkeypatch.setattr(firewall, "ensure_rule", lambda *a, **k: (True, "ok"))
        monkeypatch.setattr(lan, "_restart_service", lambda *a, **k: (True, "ok"))

        report = lan.enable_lan(info, dry_run=True, allow_public=True)
        assert report.success


class TestEnableFailureStops:
    def test_no_service_stops_immediately(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "")
        report = lan.enable_lan(info)
        assert not report.success
        # 只走了一步就停，不该继续调防火墙
        assert len(report.steps) == 1

    def test_no_network_stops(self, info, monkeypatch):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [])
        report = lan.enable_lan(info)
        assert not report.success
        assert len(report.steps) == 2

    def test_rule_failure_stops_before_config_change(self, info, monkeypatch):
        """防火墙失败就不该动 Caddyfile —— 否则服务白重启一次。"""
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("192.168.1.12", "以太网", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("home", "以太网", "Private")
        ])
        monkeypatch.setattr(firewall, "ensure_rule", lambda *a, **k: (False, "已取消"))

        called = {"restart": False}

        def _restart(*a, **k):
            called["restart"] = True
            return True, "ok"

        monkeypatch.setattr(lan, "_restart_service", _restart)

        report = lan.enable_lan(info)
        assert not report.success
        assert report.cancelled
        assert not called["restart"], "防火墙失败后不该重启服务"


class TestDisableNeverTouchesFirewall:
    """核心设计约束：关闭局域网只改监听地址，不删规则、不需要 UAC。"""

    def test_disable_does_not_call_firewall(self, info, monkeypatch, tmp_path):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")

        def _boom(*a, **k):
            raise AssertionError("disable 不应该调用任何防火墙写入")

        monkeypatch.setattr(firewall, "ensure_rule", _boom)
        monkeypatch.setattr(firewall, "remove_rules", _boom)
        monkeypatch.setattr(lan, "_restart_service", lambda *a, **k: (True, "ok"))

        # 写一份初始 Caddyfile 让流程能跑
        (tmp_path / "conf").mkdir(parents=True, exist_ok=True)
        caddyfile.write(tmp_path / "conf" / "Caddyfile", 8766, 8765, caddyfile.BIND_ALL)

        report = lan.disable_lan(info, dry_run=True)
        assert report.success
        assert any("Caddyfile" in name for name, _, _ in report.steps)

    def test_disable_writes_loopback(self, info, monkeypatch, tmp_path):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(lan, "_restart_service", lambda *a, **k: (True, "ok"))

        (tmp_path / "conf").mkdir(parents=True, exist_ok=True)
        caddyfile.write(tmp_path / "conf" / "Caddyfile", 8766, 8765, caddyfile.BIND_ALL)

        lan.disable_lan(info, dry_run=True)

        # dry-run 不该真的改文件，原配置必须原封不动
        detected = caddyfile.detect_bind_host(tmp_path / "conf" / "Caddyfile")
        assert detected == caddyfile.BIND_ALL

        # 真正执行时才写
        monkeypatch.setattr(lan, "_restart_service", lambda *a, **k: (True, "ok"))
        monkeypatch.setattr(network, "listens_on_loopback_only", lambda p: True)
        monkeypatch.setattr(lan, "probe_lan", lambda *a, **k: (True, "ok", "http://127.0.0.1/healthz"))
        monkeypatch.setattr(network, "listen_addresses", lambda p: ["127.0.0.1"])
        lan.disable_lan(info)
        assert caddyfile.detect_bind_host(tmp_path / "conf" / "Caddyfile") == caddyfile.BIND_LOOPBACK


class TestRollback:
    """改 Caddyfile 后重启失败，必须回滚到旧配置。"""

    def test_restart_failure_restores_backup(self, info, monkeypatch, tmp_path):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")

        (tmp_path / "conf").mkdir(parents=True, exist_ok=True)
        original = caddyfile.render(8766, 8765, caddyfile.BIND_ALL)
        (tmp_path / "conf" / "Caddyfile").write_text(original, encoding="utf-8", newline="")

        monkeypatch.setattr(lan, "_restart_service", lambda *a, **k: (False, "启动失败"))

        report = lan.disable_lan(info)
        assert not report.success

        restored = (tmp_path / "conf" / "Caddyfile").read_text(encoding="utf-8")
        assert restored == original, "重启失败后 Caddyfile 必须回滚"


class TestDryRun:
    def test_enable_dry_run_reports_without_side_effects(self, info, monkeypatch, tmp_path):
        monkeypatch.setattr(firewall, "service_image_path", lambda n: "C:\\x\\caddy.exe")
        monkeypatch.setattr(network, "list_lan_addresses", lambda: [
            network.LanAddress("192.168.1.12", "以太网", 24, True, 1)
        ])
        monkeypatch.setattr(network, "list_network_profiles", lambda: [
            network.NetworkProfile("home", "以太网", "Private")
        ])
        monkeypatch.setattr(firewall, "ensure_rule", lambda *a, **k: (True, "[dry-run]"))
        monkeypatch.setattr(lan, "_restart_service", lambda *a, **k: (True, "[dry-run]"))
        monkeypatch.setattr(lan, "probe_lan", lambda *a, **k: pytest.fail("dry-run 不该真探测"))

        report = lan.enable_lan(info, dry_run=True)
        assert report.success
        assert any("[dry-run]" in d for _, _, d in report.steps)


# ──────────────────────────────────────────────────────────────
# 安装信息解析
# ──────────────────────────────────────────────────────────────


class TestLoadInstallInfo:
    def test_explicit_params_win(self, monkeypatch):
        monkeypatch.setattr(lan, "read_registry", lambda: {"ApiPort": "9999"})
        monkeypatch.setattr(lan, "detect_service_prefix", lambda *a: "detected")
        info = lan.load_install_info(
            root="C:\\x", api_port=1111, web_port=2222, service_prefix="explicit"
        )
        assert info.api_port == 1111
        assert info.web_port == 2222
        assert info.service_prefix == "explicit"

    def test_registry_used_when_no_params(self, monkeypatch):
        monkeypatch.setattr(lan, "read_registry", lambda: {
            "Install": "C:\\Program Files\\LAN Share",
            "DataFolder": "C:\\ProgramData\\LANShare\\data",
            "ApiPort": "9911",
            "WebPort": "9912",
            "Prefix": "lanshare",
        })
        info = lan.load_install_info()
        assert info.root == __import__("pathlib").Path("C:\\Program Files\\LAN Share")
        assert info.api_port == 9911
        assert info.web_port == 9912
        assert info.service_prefix == "lanshare"

    def test_detected_prefix_used_when_registry_empty(self, monkeypatch):
        """装机版没写注册表时，靠探测本机服务名（生产是 lan-share）。"""
        monkeypatch.setattr(lan, "read_registry", lambda: {})
        monkeypatch.setattr(lan, "detect_service_prefix", lambda *a: "lan-share")
        info = lan.load_install_info()
        assert info.service_prefix == "lan-share"
        assert info.api_service == "lan-share-api"

    def test_invalid_port_falls_back(self, monkeypatch):
        monkeypatch.setattr(lan, "read_registry", lambda: {"ApiPort": "不是数字"})
        monkeypatch.setattr(lan, "detect_service_prefix", lambda *a: "x")
        info = lan.load_install_info()
        assert info.api_port == 8766
