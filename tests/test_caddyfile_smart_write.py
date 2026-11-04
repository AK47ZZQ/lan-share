"""T2：install 守卫 + 原子写 Caddyfile。"""
import os

import pytest

from lanshare_cli import caddyfile

_LEGACY = """\
# 手写配置，没有 lanshare_cli 标记
:8765 {
\ttls /certs/full.pem /certs/key.pem
\treverse_proxy 127.0.0.1:8766
}

:8801 {
\trespond "legacy extra" 200
}
"""

_OURS = """\
# 由 lanshare_cli 生成，不要手改
{
\tauto_https disable_redirects
}

https://localhost:8765 {
\tbind 127.0.0.1
}
"""


class TestSmartWrite:
    """install 与 lan enable/disable 共用的「改 Caddyfile」守卫策略。"""

    def test_custom_config_preserved(self, tmp_path):
        """非我们生成的配置 → 只改 bind，其余逐字节保留。"""
        f = tmp_path / "Caddyfile"
        f.write_text(_LEGACY, encoding="utf-8")
        mode = caddyfile.smart_write(
            f, api_port=8766, web_port=8765, bind_host=caddyfile.BIND_LOOPBACK,
        )
        assert mode == "bind-only"
        text = f.read_text(encoding="utf-8")
        # 模板特征一行都不能出现 —— 出现即「整份重写」
        assert "由 lanshare_cli 生成" not in text
        assert "issuer internal" not in text
        assert ":8801 {" in text, "8801 站点块被抹掉"
        assert "tls /certs/full.pem" in text, "外挂证书配置被抹掉"
        assert "reverse_proxy 127.0.0.1:8766" in text, "反代行被抹掉"
        # bind 行被加上
        assert "bind 127.0.0.1" in text

    def test_ours_config_full_rewrite(self, tmp_path):
        f = tmp_path / "Caddyfile"
        f.write_text(_OURS, encoding="utf-8")
        mode = caddyfile.smart_write(
            f, api_port=8766, web_port=8765, bind_host=caddyfile.BIND_ALL,
        )
        assert mode == "rewritten"
        text = f.read_text(encoding="utf-8")
        assert "由 lanshare_cli 生成" in text

    def test_backup_created_before_rewrite(self, tmp_path):
        """重写前必须留 Caddyfile.bak-<时间戳> 备份。"""
        f = tmp_path / "Caddyfile"
        f.write_text(_OURS, encoding="utf-8")
        caddyfile.smart_write(f, api_port=8766, web_port=8765,
                              bind_host=caddyfile.BIND_ALL)
        baks = sorted(tmp_path.glob("Caddyfile.bak-*"))
        assert baks, "没留备份"
        assert _OURS in baks[0].read_text(encoding="utf-8")

    def test_backup_created_for_custom_config_too(self, tmp_path):
        f = tmp_path / "Caddyfile"
        f.write_text(_LEGACY, encoding="utf-8")
        caddyfile.smart_write(f, api_port=8766, web_port=8765,
                              bind_host=caddyfile.BIND_LOOPBACK)
        baks = sorted(tmp_path.glob("Caddyfile.bak-*"))
        assert baks and _LEGACY in baks[0].read_text(encoding="utf-8")

    def test_custom_without_site_block_raises(self, tmp_path):
        """自定义配置里找不到目标端口站点块 → 报错而不是硬改。"""
        f = tmp_path / "Caddyfile"
        f.write_text("# 手写配置\n:9999 {\n}\n", encoding="utf-8")
        with pytest.raises(ValueError):
            caddyfile.smart_write(f, api_port=8766, web_port=8765,
                                  bind_host=caddyfile.BIND_LOOPBACK)
        # 文件原样没动
        assert f.read_text(encoding="utf-8").startswith("# 手写配置")

    def test_atomic_write_survives_replace_failure(self, tmp_path, monkeypatch):
        """os.replace 失败时旧文件必须原封不动（临时文件方案的证据）。"""
        f = tmp_path / "Caddyfile"
        f.write_text(_OURS, encoding="utf-8")
        real_replace = os.replace

        def boom(src, dst):
            raise OSError("replace failed")
        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            caddyfile.write(f, api_port=8766, web_port=8765)
        assert f.read_text(encoding="utf-8") == _OURS
        # 临时文件清干净
        leftovers = [p for p in tmp_path.iterdir() if p.name != "Caddyfile"]
        assert not leftovers, "临时文件没清：" + str(leftovers)

    def test_write_no_temp_leftover(self, tmp_path):
        """正常写完不留 .tmp 残留。"""
        f = tmp_path / "Caddyfile"
        caddyfile.write(f, api_port=8766, web_port=8765)
        text = f.read_text(encoding="utf-8")
        leftovers = [p.name for p in f.parent.iterdir() if p.name != "Caddyfile"]
        assert not leftovers, "残留：" + str(leftovers)
        assert "由 lanshare_cli 生成" in text

    def test_lan_aliases_intact(self):
        """lan.py 的 _is_ours / _rewrite_bind_in_place 必须还能用（别处调着）。"""
        from lanshare_cli import lan
        assert lan._is_ours(_OURS)
        assert not lan._is_ours(_LEGACY)
        new, ok_flag = lan._rewrite_bind_in_place(_LEGACY, caddyfile.BIND_LOOPBACK, 8765)
        assert ok_flag and "bind 127.0.0.1" in new


class TestInstallGuard:
    """cli.py install 的第 6 步：legacy 部署升级不能丢整份配置。"""

    def _layout(self, tmp_path):
        from lanshare_cli import cli
        return cli.Layout(root=tmp_path, data_dir=tmp_path / "data",
                          service_prefix="t")

    def _run_install_step(self, tmp_path):
        """驱动 cli.py 里抽出来的 Caddyfile 步骤函数（0 = 成功）。"""
        from lanshare_cli import cli
        layout = self._layout(tmp_path)
        return cli.write_caddyfile_step(layout, api_port=8766, web_port=8765)

    def test_install_keeps_legacy_config(self, tmp_path):
        """升级到带守卫的版本：legacy 配置只改 bind，域名/证书/8801 全保留。"""
        f = tmp_path / "conf" / "Caddyfile"
        f.parent.mkdir(parents=True)
        f.write_text(_LEGACY, encoding="utf-8")
        rc = self._run_install_step(tmp_path)
        assert rc == 0
        text = f.read_text(encoding="utf-8")
        assert "由 lanshare_cli 生成" not in text
        assert ":8801 {" in text
        assert "tls /certs/full.pem" in text
        # legacy 无 bind 行 + detect_bind_host→BIND_ALL → 内容应逐字节保留
        assert text == _LEGACY

    def test_install_rewrites_ours(self, tmp_path):
        """我们生成的配置 → 照旧整份重写（升级路径不回归）。"""
        f = tmp_path / "conf" / "Caddyfile"
        f.parent.mkdir(parents=True)
        f.write_text(_OURS, encoding="utf-8")
        rc = self._run_install_step(tmp_path)
        assert rc == 0
        text = f.read_text(encoding="utf-8")
        assert "由 lanshare_cli 生成" in text, "我们自己的配置被守卫拦住了"
        assert "issuer internal" in text

    def test_install_new_install_full_generate(self, tmp_path):
        """全新安装（没有 Caddyfile）→ 正常生成。"""
        rc = self._run_install_step(tmp_path)
        assert rc == 0
        f = tmp_path / "conf" / "Caddyfile"
        assert f.is_file()
        assert "由 lanshare_cli 生成" in f.read_text(encoding="utf-8")

    def test_install_ours_config_keeps_backup(self, tmp_path):
        """我们的配置重写前也备份（后悔药对两种路径都生效）。"""
        f = tmp_path / "conf" / "Caddyfile"
        f.parent.mkdir(parents=True)
        f.write_text(_OURS, encoding="utf-8")
        self._run_install_step(tmp_path)
        baks = sorted((tmp_path / "conf").glob("Caddyfile.bak-*"))
        assert baks, "install 路径没留备份"


class TestWriteIdempotent:
    """T10：连续 write 3 次字节必须相同 —— 升级/开关反复触发不产生 diff 噪音，
    也证明 write 不依赖「上次写过什么」这种隐形状态。"""

    def test_three_writes_identical_bytes(self, tmp_path):
        f = tmp_path / "Caddyfile"
        caddyfile.write(f, api_port=8766, web_port=8765)
        first = f.read_bytes()
        for _ in range(2):
            caddyfile.write(f, api_port=8766, web_port=8765)
        assert f.read_bytes() == first
