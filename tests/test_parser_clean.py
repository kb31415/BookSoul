"""清洗测试（`MVP_PLAN.md` 阶段 2 任务 1：去空行、去广告行）。

原则：**只做确定性清洗，不猜语义** —— 宁可留一点噪声，也不能误删正文。
"""

from __future__ import annotations

from booksoul.ingest import clean_text, is_ad_line

AD_LINES = [
    "请记住本站域名 www.example-ad.com",
    "手机用户请访问 m.example-ad.com",
    "最新章节目录 请记住本站",
    "笔趣阁 全本小说无弹窗",
    "https://www.example-ad.com/book/123",
    "www.example-ad.com",
    "求月票",
    "（本章完）",
    "加入书签 方便下次阅读",
]


def test_ad_lines_are_detected() -> None:
    for line in AD_LINES:
        assert is_ad_line(line) is True, line


CONTENT_LINES = [
    "他握紧了剑，雨水顺着剑脊滑落。",
    "「站在那儿别动。」",
    "第一章节的写法有点怪，但这是正文。",
    "他把书签夹回了第一百页。",
    "月票这两个字，他只在信里写过一次。",
    "本章完结的时候，天已经亮了。",
    "她想要一个域名之外的答案。",
    "他点了一下手机，屏幕却黑了。",
]


def test_normal_prose_is_not_treated_as_ad() -> None:
    """误删正文比留噪声更糟 —— 这些常见说法都不能被判成广告。"""
    for line in CONTENT_LINES:
        assert is_ad_line(line) is False, line


def test_blank_line_is_not_an_ad() -> None:
    assert is_ad_line("") is False
    assert is_ad_line("   ") is False


def test_clean_removes_ad_lines() -> None:
    text = "第一章 雨夜\n请记住本站域名 x.com\n他握着剑。\n"

    cleaned = clean_text(text)

    assert "请记住本站" not in cleaned
    assert "他握着剑。" in cleaned


def test_clean_removes_standalone_url_lines() -> None:
    cleaned = clean_text("他握着剑。\nhttps://spam.example.com/a\n她笑了。\n")

    assert "spam.example.com" not in cleaned
    assert "他握着剑。" in cleaned and "她笑了。" in cleaned


def test_clean_normalises_crlf_and_cr() -> None:
    cleaned = clean_text("第一章\r\n他握着剑。\r她笑了。\r\n")

    assert "\r" not in cleaned
    assert cleaned.split("\n") == ["第一章", "他握着剑。", "她笑了。"]


def test_clean_collapses_blank_runs_to_single_blank() -> None:
    cleaned = clean_text("甲。\n\n\n\n\n乙。\n")

    assert cleaned == "甲。\n\n乙。"


def test_clean_strips_trailing_whitespace_per_line() -> None:
    cleaned = clean_text("甲。   \n乙。\t\n")

    assert cleaned == "甲。\n乙。"


def test_clean_strips_outer_whitespace() -> None:
    cleaned = clean_text("\n\n  甲。  \n\n")

    assert cleaned == "甲。"


def test_clean_keeps_paragraph_breaks() -> None:
    """空行要保留一个 —— 阶段 4 检索、阶段 7 记忆都靠段落边界切。"""
    cleaned = clean_text("第一段。\n\n第二段。\n")

    assert "\n\n" in cleaned


def test_clean_keeps_inner_blank_only_once_across_ad_removal() -> None:
    cleaned = clean_text("甲。\n\n请记住本站\n\n乙。\n")

    assert cleaned == "甲。\n\n乙。"


def test_clean_is_idempotent() -> None:
    text = "第一章 雨夜\n\n请记住本站\n\n他握着剑。\n\n\n她笑了。\n"

    once = clean_text(text)

    assert clean_text(once) == once


def test_clean_empty_text() -> None:
    assert clean_text("") == ""
    assert clean_text("\n\n\n") == ""


def test_fullwidth_space_line_is_blank() -> None:
    """全角空格行也算空行 —— 压成一个空行（段落边界要保住，不能直接删掉）。"""
    cleaned = clean_text("甲。\n　\n乙。\n")

    assert cleaned == "甲。\n\n乙。"
