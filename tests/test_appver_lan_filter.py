"""`_lan_addresses()` 的同网段过滤（2026-11-16）。

背景：这台生产机实测把 WSL / Hyper-V 虚拟网卡（10.99.1.1 /
10.99.2.1 / 10.99.3.1）连同真实以太网一起下发给 App，App 每轮
自动再发现都要并行探测这些永远连不上的地址。

两条不变量：
1. 有与出网口同网段的候选时，**只**下发同网段（虚拟网卡被彻底排除）；
2. 拿不到出网口 IP 时 fail-open —— 退回旧行为，绝不能因为探测失败就
   一个候选都不给（那会让 App 的再发现彻底失效，比多给几个死地址更糟）。
"""
import pytest

from app.api import appver


def _infos(*ips):
    return [(None, None, None, None, (ip, 0)) for ip in ips]


def test_lan_addresses_only_same_subnet_as_egress(monkeypatch):
    monkeypatch.setattr(
        appver.socket, "getaddrinfo",
        lambda *a, **k: _infos("10.10.20.12", "10.99.1.1",
                               "10.99.2.1", "10.99.3.1"))
    monkeypatch.setattr(appver, "_egress_ip", lambda: "10.10.20.12")

    assert appver._lan_addresses() == ["https://10.10.20.12:8765"]


def test_lan_addresses_keeps_both_cards_on_same_subnet(monkeypatch):
    """有线 + WLAN 同在 192.168.1.x 时两个都要给（换网卡不该失联）。"""
    monkeypatch.setattr(
        appver.socket, "getaddrinfo",
        lambda *a, **k: _infos("10.10.20.12", "10.10.20.11", "10.99.2.1"))
    monkeypatch.setattr(appver, "_egress_ip", lambda: "10.10.20.12")

    got = appver._lan_addresses()
    assert set(got) == {"https://10.10.20.12:8765", "https://10.10.20.11:8765"}


def test_lan_addresses_fail_open_without_egress(monkeypatch):
    """拿不到出网口 IP：退回旧行为（RFC1918 全收），绝不返回空表。"""
    monkeypatch.setattr(
        appver.socket, "getaddrinfo",
        lambda *a, **k: _infos("10.10.20.12", "10.99.2.1"))
    monkeypatch.setattr(appver, "_egress_ip", lambda: "")

    got = appver._lan_addresses()
    assert len(got) == 2, got
    assert "https://10.10.20.12:8765" in got


@pytest.mark.parametrize("ip", ["8.8.8.8", "127.0.0.1", "not-an-ip"])
def test_lan_addresses_drops_non_private(monkeypatch, ip):
    """非 RFC1918 一律不进候选（原行为，回归保护）。"""
    monkeypatch.setattr(appver.socket, "getaddrinfo",
                        lambda *a, **k: _infos(ip))
    monkeypatch.setattr(appver, "_egress_ip", lambda: "")

    assert appver._lan_addresses() == []
