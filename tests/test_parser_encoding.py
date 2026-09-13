"""编码探测测试（`MVP_PLAN.md` 阶段 2 任务 1）。

验收标准里明确的一条：**GBK 文件能正确读出**。
"""

from __future__ import annotations

import pytest

from booksoul.ingest import DEFAULT_ENCODINGS, decode_bytes, detect_encoding

GBK_TEXT = "第一章 雨夜\n他握紧了剑，雨水顺着剑脊滑落。\n「站在那儿别动。」\n"


def test_utf8_detected() -> None:
    assert detect_encoding(GBK_TEXT.encode("utf-8")) == "utf-8"


def test_gbk_detected() -> None:
    """纯中文的 GBK 字节不是合法 utf-8，必须被识别成 gb18030 系。"""
    data = GBK_TEXT.encode("gbk")

    assert detect_encoding(data) in {"gb18030", "gbk"}


def test_gb18030_detected_for_gbk_bytes() -> None:
    """探测用宽的那层（gb18030 是 gbk 超集），能解出就算命中。"""
    data = GBK_TEXT.encode("gb18030")

    assert detect_encoding(data) == "gb18030"


def test_utf8_bom_detected() -> None:
    data = b"\xef\xbb\xbf" + GBK_TEXT.encode("utf-8")

    assert detect_encoding(data) == "utf-8-sig"


def test_utf16_bom_detected() -> None:
    data = GBK_TEXT.encode("utf-16")  # Python 会写入 BOM

    assert detect_encoding(data) == "utf-16"


def test_empty_bytes_is_utf8() -> None:
    assert detect_encoding(b"") == "utf-8"


def test_ascii_only_is_utf8() -> None:
    assert detect_encoding(b"Chapter 1\nHello.\n") == "utf-8"


def test_utf8_wins_over_gb18030_for_chinese() -> None:
    """关键顺序问题：gb18030 能"解出"任意 utf-8 字节，所以 utf-8 必须排在前面。

    如果顺序反了，这里会得到 gb18030 + 一堆乱码。
    """
    data = "他说：「不必问。」".encode("utf-8")

    assert detect_encoding(data) == "utf-8"
    assert decode_bytes(data)[0] == "他说：「不必问。」"


def test_decode_with_explicit_encoding_skips_detection() -> None:
    data = GBK_TEXT.encode("gbk")
    text, encoding = decode_bytes(data, "gbk")

    assert encoding == "gbk"
    assert "站在那儿别动" in text


def test_decode_bytes_uses_detection_by_default() -> None:
    text, encoding = decode_bytes(GBK_TEXT.encode("gbk"))

    assert "站在那儿别动" in text
    assert not text.startswith("\ufeff")
    assert encoding in {"gb18030", "gbk"}


def test_decode_strips_utf8_bom() -> None:
    text, encoding = decode_bytes(b"\xef\xbb\xbf" + "第一章".encode("utf-8"))

    assert encoding == "utf-8-sig"
    assert text.startswith("第一章")


def test_decode_undecodable_bytes_does_not_raise() -> None:
    """垃圾字节不能让主流程崩 —— 兜底 gb18030 + replace。"""
    text, encoding = decode_bytes(b"\xff\xfe\xfd\xfc\x80\x81\x82")

    assert encoding == "gb18030"
    assert isinstance(text, str)


def test_decode_unknown_encoding_name_falls_back() -> None:
    text, encoding = decode_bytes("第一章".encode("utf-8"), "no-such-codec")

    assert encoding == "gb18030"
    assert isinstance(text, str)


def test_default_candidates_order_is_strict_first() -> None:
    """utf-8 必须在 gb18030 之前（见 test_utf8_wins_over_gb18030_for_chinese）。"""
    assert DEFAULT_ENCODINGS.index("utf-8") < DEFAULT_ENCODINGS.index("gb18030")


def test_gbk_round_trip_via_file(tmp_path) -> None:
    """端到端：GBK 文件 → 正确的中文（不复现乱码）。"""
    from booksoul.ingest import parse_file

    path = tmp_path / "gbk_novel.txt"
    path.write_bytes(GBK_TEXT.encode("gbk"))

    novel = parse_file(path)

    assert novel.chapters, "GBK 文件应至少切出一章"
    assert "站在那儿别动" in novel.chapters[0].content
    assert "锟斤拷" not in novel.chapters[0].content
