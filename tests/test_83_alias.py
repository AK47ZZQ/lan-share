"""8.3 短名别名拦截钉（v3.67.4 P0，真机实证）。

本卷 NTFS 短名生成未关：`.meta.db`→`META~1.DB`、`.trash`→`TRASH~1`、
`.backups`→`BACKUP~1`、`.thumbs`→`THUMBS~1`。短名不以点开头，只拦点前缀
时成员可经 `GET /api/files/BACKUP~1/...` 列举并下载元数据库备份
（口令哈希+会话令牌=全账号接管）；2026-09-27 真机实测 BACKUP~1/ 列举 200。
消融锚：摘掉 safe_path / _clean_parts 的 _83_ALIAS_RE 检查 → 全红。
"""
import pytest
from fastapi import HTTPException

from app import spaces
from app.filetools import safe_path


def test_safe_path_rejects_83_aliases():
    for evil in ("META~1.DB", "TRASH~1/x.jpg", "BACKUP~1/meta-20260917.db",
                 "shared/sub/THUMBS~1/x.jpg", "zips~1/a.zip"):
        with pytest.raises(HTTPException) as ei:
            safe_path(evil)
        assert ei.value.status_code == 403, evil


def test_clean_parts_rejects_83_aliases():
    for evil in ("TRASH~1", "x/META~1.DB"):
        with pytest.raises(HTTPException) as ei:
            spaces._clean_parts(evil)
        assert ei.value.status_code == 403


def test_legit_paths_still_pass():
    # Word 锁文件形态 ~$ 不匹配 8.3 别名正则，必须放行
    assert spaces._clean_parts("shared/~$doc.docx") == ["shared", "~$doc.docx"]
    assert safe_path("shared/backup.zip").name == "backup.zip"


def test_live_api_meta_db_backup_blocked_via_alias(auth):
    """真机回归钉（P0）：8.3 别名列举/下载必须 403（修复前 200）。"""
    for p in ("BACKUP~1/", "BACKUP~1/meta-20260917-195112.db", "TRASH~1/"):
        r = auth.get(f"/api/files/{p}", headers={"Host": "lan.local:8765"})
        assert r.status_code == 403, (p, r.status_code)
