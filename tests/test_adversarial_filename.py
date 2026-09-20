"""对抗式审查·文件名清洗器边界（2026-09-20 下午批次）。

不变量（`clean_filename` 自己的注释就写明了）：结尾点/空格必须剥掉，因为
**NTFS 会静默剥离**——若不剥，登记名与磁盘实际名就不是同一个文件，
后续的 stat/改名/删除会指向别处或 404。

缺陷：剥离发生在截断**之前**（`rstrip('. ')` 然后 `[:200]`），所以足够长的
名字可以在第 200 个字符处重新落出点/空格，把不变量又破掉。
"""

import pytest

from app.filetools import clean_filename

# 203 字符：无非法字符、不以点结尾 → rstrip 不动它 → 截到 200 时正好落在点上
LONG_DOT = "a" * 199 + ".txt"
LONG_SPACE = "a" * 199 + " x"


def test_clean_filename_never_ends_with_dot():
    assert not clean_filename(LONG_DOT).endswith(".")


def test_clean_filename_never_ends_with_space():
    assert not clean_filename(LONG_SPACE).endswith(" ")


def test_clean_filename_is_idempotent():
    """清洗必须幂等：目录列表把已落盘的名字再送回接口时不得二次变形，
    否则改名一次就换一个名字。"""
    for raw in (LONG_DOT, LONG_SPACE, "正常名字.txt", "..", "con", ".hidden", "x" * 500):
        once = clean_filename(raw)
        assert clean_filename(once) == once


def test_clean_filename_keeps_existing_guarantees():
    """这些是它本来就守住的，钉住以免"修截断"时把别的规则改坏。"""
    assert clean_filename(None) == "unnamed"
    assert clean_filename("") == "unnamed"
    assert clean_filename("...") == "unnamed"
    assert clean_filename("a/../b") == "a_.._b"          # 分隔符与点段中和
    assert clean_filename("con.txt") == "_con.txt"        # Windows 保留名
    assert clean_filename("NUL") == "_NUL"
    assert clean_filename("nul.txt") == "_nul.txt"
    assert clean_filename(".hidden") == "_.hidden"        # 前导点（会藏文件）
    assert clean_filename("a\x01b") == "a_b"              # 控制符
    # 只中和 `:` 就够：备用数据流必须是 name:stream 语法，冒号没了 $ 就是普通字符
    assert clean_filename("a:b:$DATA") == "a_b_$DATA"
    assert len(clean_filename("x" * 500)) <= 200
    assert clean_filename("报告 final v2 (copy).pdf") == "报告 final v2 (copy).pdf"


def test_clean_filename_truncation_keeps_extension_when_short():
    """常规长度下不得有无谓变形——修 bug 不能顺带改语义。"""
    assert clean_filename("photo.jpg") == "photo.jpg"
    assert clean_filename("a" * 190 + ".jpg") == "a" * 190 + ".jpg"


@pytest.mark.parametrize("raw", [
    "a" * 199 + ".", "a" * 199 + " ", "a" * 199 + ". ",
    "a" * 198 + "  ", "。" * 199 + ".",
])
def test_clean_filename_boundary_inputs(raw):
    out = clean_filename(raw)
    assert out and not out.endswith((".", " "))
    assert len(out) <= 200


# ─── 端到端：清洗器正确 ≠ 写路径调用了它 ───
# 上面全是纯函数断言。真正的失效模式是"某条写路径漏调 clean_filename"——
# 那需要过一次 HTTP。M15 消融实验暴露这个缺口是有意义的：设备名的写入
# **不报错**（字节进控制台），所以漏一次就是"客户端看到成功、盘上没有文件"。
# 用 mkdir 而非 upload 来钉：`os.mkdir('…\\con')` 会立刻抛错→500，测试能干净地红；
# 而真的往 con 写内容会让用例挂住而不是失败（本会话实测 3/3 >90s），
# 那等于往门禁里埋一颗不定时的雷，故不在此覆盖（已挂账）。
@pytest.mark.parametrize("raw,expect", [("con", "_con"), ("NUL", "_NUL"),
                                        ("lpt1.txt", "_lpt1.txt")])
def test_mkdir_api_path_actually_sanitizes(auth, raw, expect):
    import os
    import shutil
    from app.config import SHARE_ROOT

    r = auth.post(f"/api/mkdir/{raw}")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == expect, "接口返回的名字没被清洗"
    # 盘上真相只能用 listdir：Path('...\\con').exists() 对设备名**恒为 True**
    # （2026-09-20 实测），拿 exists() 断言"没建出 con"会得到反向结论。
    entries = os.listdir(SHARE_ROOT)
    assert expect in entries, "目录没真正落盘"
    assert raw not in entries and raw.lower() not in [e.lower() for e in entries]
    shutil.rmtree(SHARE_ROOT / expect, ignore_errors=True)   # 自建自清
