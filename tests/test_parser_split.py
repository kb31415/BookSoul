"""分章测试（`MVP_PLAN.md` 阶段 2 任务 1–2）。

验收标准：**切出的章节数与目测一致**。样例语料由 `conftest.make_sample_novel_text()`
构造，章节数用程序计数可知（5 章）。
"""

from __future__ import annotations

import pytest

from booksoul.ingest import (
    CHAPTER_TITLE_MAX_LENGTH,
    Chapter,
    Novel,
    clean_text,
    find_chapter_title,
    parse_text,
    split_long_chapters,
    split_text,
)
from conftest import make_sample_novel_text


def _make_novel(chapters: list[tuple[str, str]], head: str = "") -> str:
    parts = [head] if head else []
    for title, body in chapters:
        parts.append(f"{title}\n{body}")
    return "\n".join(parts) + "\n"


# ────────────────────────── 标题识别 ──────────────────────────

ACCEPTED_TITLES = [
    "第一章 雨夜",
    "第1章 雨夜",
    "第 1 章 雨夜",
    "第１２章 雨夜",          # 全角数字
    "第十二章",
    "第二十三章 旧约",
    "第一百零五章 长夜",
    "第3节 出发",
    "第7回 初遇",
    "第二篇 风起",
    "第一卷 青云",
    "第4集 剑冢",
    "第一幕 夜雨",
    "序章",
    "楔子",
    "引子",
    "尾声",
    "番外",
    "番外 之后的事",
    "第一章：雨夜",            # 全角冒号
    "第一章、雨夜",
    "第一章.雨夜",
]


@pytest.mark.parametrize("line", ACCEPTED_TITLES)
def test_recognises_chapter_titles(line: str) -> None:
    assert find_chapter_title(line) == line


REJECTED_LINES = [
    "他握紧了剑，雨水顺着剑脊滑落。",
    "第一章节的写法有点怪，但这是正文。",     # "第一章节"不是标题
    "他把第三章读了三遍，还是不懂。",          # 标题不在行首
    "序章的写法他改了三遍。",                  # "序章"后面是句子
    "尾声之后的故事还很长。",                  # 同理
    "最后一章",                                # "最后"不是数字，本来就不是标题
    "最后一章里他死了。",
    "下一章见。",
    "",
    "   ",
    "序",                                     # 单字太容易误伤，不认
]


@pytest.mark.parametrize(
    "line",
    ["番外 之后的事", "尾声：之后", "序章", "第十二章 旧约"],
)
def test_boundary_cases_still_recognised_as_titles(line: str) -> None:
    """和 REJECTED_LINES 只差一两个汉字 —— 这几条必须是标题，别矫枉过正。"""
    assert find_chapter_title(line) == line


@pytest.mark.parametrize("line", REJECTED_LINES)
def test_rejects_non_titles(line: str) -> None:
    assert find_chapter_title(line) is None


def test_rejects_overlong_title_line() -> None:
    """正文里恰好出现"第一章"、后面还拖很长一串 → 不是标题。"""
    line = "第一章" + "正" * (CHAPTER_TITLE_MAX_LENGTH + 20)

    assert find_chapter_title(line) is None


def test_title_keeps_its_own_formatting() -> None:
    """标题整行原样保留（阶段 3 直接把它填进 prompt 的 {chapter_title}）。"""
    assert find_chapter_title("  第二章　剑冢  ") == "第二章　剑冢"


# ────────────────────────── 正常分章 ──────────────────────────


def test_sample_novel_has_expected_chapter_count(sample_novel_text: str) -> None:
    """程序计数：样例里有 5 个章节标题 → 必须切出 5 章。"""
    novel = parse_text(sample_novel_text, book_id="sample")

    assert len(novel.chapters) == 5
    assert [chapter.title for chapter in novel.chapters] == [
        "第一章 雨夜",
        "第二章 剑冢",
        "第三章 旧约",
        "第四章 叛徒",
        "第五章 长夜",
    ]


def test_chapter_index_is_contiguous_from_zero(sample_novel_text: str) -> None:
    """index 必须从 0 连续 —— 阶段 3 的 `chapters/{i}.names.json` 靠它对齐。"""
    novel = parse_text(sample_novel_text, book_id="sample")

    assert [chapter.index for chapter in novel.chapters] == [0, 1, 2, 3, 4]


def test_chapter_content_contains_only_body(sample_novel_text: str) -> None:
    """标题不能混进 content（否则阶段 3 会把标题重复喂给模型）。"""
    novel = parse_text(sample_novel_text, book_id="sample")
    first, second = novel.chapters[0], novel.chapters[1]

    assert first.title not in first.content
    assert second.title not in second.content
    assert first.content.startswith("第1章正文。")
    assert second.content.startswith("第2章正文。")


def test_chapter_content_matches_declared_length(sample_novel_text: str) -> None:
    """每章正文 = 「第{i}章正文。」× 40 → 长度可精确校验（每句 6 字）。"""
    novel = parse_text(sample_novel_text, book_id="sample")

    for position, chapter in enumerate(novel.chapters, start=1):
        assert len(chapter.content) == len(f"第{position}章正文。") * 40
        assert len(chapter.content) == 6 * 40


def test_no_content_is_lost_or_duplicated() -> None:
    """所有章节正文长度之和 == 清洗后全文 - 章节标题行 - 行间换行符。

    用**不带卷首**的语料，才能把口径算干净（卷首是否保留另有用例覆盖）。
    """
    text = _make_novel(
        [
            (f"第{i}章 标题{i}", f"第{i}章正文。" * 40)
            for i in range(1, 6)
        ]
    )
    cleaned = clean_text(text)
    novel = parse_text(text, book_id="sample")

    lines = cleaned.split("\n")
    separators = len(cleaned) - sum(len(line) for line in lines)  # 每个标题行后一个换行
    overhead = sum(len(chapter.title) for chapter in novel.chapters) + separators

    assert separators == 2 * len(novel.chapters) - 1
    assert sum(len(c.content) for c in novel.chapters) == len(cleaned) - overhead


def test_chapter_content_has_no_ads(sample_novel_text: str) -> None:
    novel = parse_text(sample_novel_text, book_id="sample")
    joined = "\n".join(chapter.content for chapter in novel.chapters)

    for ad in ("请记住本站", "手机用户请访问", "最新章节目录", "（本章完）"):
        assert ad not in joined


def test_short_head_is_dropped() -> None:
    """标题之前的短内容（书名 / 作者 / 广告）当噪声丢掉。"""
    text = _make_novel(
        [("第一章 雨夜", "他握着剑。")],
        head="《青云旧事》\n作者：无名",
    )

    novel = parse_text(text, book_id="x")

    assert len(novel.chapters) == 1
    assert novel.chapters[0].title == "第一章 雨夜"


def test_long_head_becomes_volume_preface() -> None:
    """卷首内容够长就该留下 —— 不能被当噪声吃掉。"""
    text = _make_novel(
        [("第一章 雨夜", "他握着剑。")],
        head="这是一段很长的卷首语。" * 40,
    )

    novel = parse_text(text, book_id="x")

    assert len(novel.chapters) == 2
    assert novel.chapters[0].title == "（卷首）"
    assert novel.chapters[1].title == "第一章 雨夜"


def test_special_titles_split_normally() -> None:
    text = _make_novel(
        [
            ("楔子", "很久以前。"),
            ("第一章 雨夜", "他握着剑。"),
            ("番外", "很多年以后。"),
        ]
    )

    novel = parse_text(text, book_id="x")

    assert [c.title for c in novel.chapters] == ["楔子", "第一章 雨夜", "番外"]


def test_single_chapter_novel() -> None:
    novel = parse_text("第一章 唯一\n只有一章。\n", book_id="x")

    assert len(novel.chapters) == 1
    assert novel.chapters[0].content == "只有一章。"


def test_empty_chapters_are_kept_as_empty_strings() -> None:
    """两章挨着（中间没正文）不应崩，也不应互相吞并。"""
    novel = parse_text("第一章 甲\n第二章 乙\n有正文了。\n", book_id="x")

    assert len(novel.chapters) == 2
    assert novel.chapters[0].content == ""
    assert novel.chapters[1].content == "有正文了。"


def test_blank_input_yields_no_chapters() -> None:
    assert parse_text("", book_id="x").chapters == []
    assert parse_text("\n\n\n", book_id="x").chapters == []


def test_chapters_are_chapter_models(sample_novel_text: str) -> None:
    novel = parse_text(sample_novel_text, book_id="sample")

    assert isinstance(novel, Novel)
    assert all(isinstance(chapter, Chapter) for chapter in novel.chapters)


# ────────────────────────── 兜底：按长度切 ──────────────────────────


def test_fallback_by_length_when_no_titles() -> None:
    """没有章节标题 → 按长度切，不产出空列表（风险预案：不阻塞主流程）。"""
    text = "这是一段没有章节标题的正文。" * 400

    novel = parse_text(text, book_id="x", fallback_length=500)

    assert len(novel.chapters) > 1
    assert all(chapter.title.endswith("部分") for chapter in novel.chapters)
    assert novel.chapters[0].title == "第1部分"
    assert [c.index for c in novel.chapters] == list(range(len(novel.chapters)))


def test_fallback_respects_length_budget() -> None:
    text = "句子。" * 1000

    chapters = split_text(text, length=100)

    assert all(len(chapter.content) <= 100 for chapter in chapters)


def test_fallback_splits_on_sentence_boundary() -> None:
    """切点尽量落在句号 / 换行上，别把句子劈成两半。"""
    text = "甲。" * 50 + "乙。" * 50

    chapters = split_text(text, length=60)

    assert len(chapters) >= 2
    assert chapters[0].content.endswith("。")


def test_fallback_handles_text_shorter_than_length() -> None:
    chapters = split_text("很短。", length=1000)

    assert len(chapters) == 1
    assert chapters[0].content == "很短。"


def test_fallback_without_sentence_breaks() -> None:
    """一长串没有标点的文本也必须能切（不能死循环）。"""
    chapters = split_text("字" * 250, length=100)

    assert [len(chapter.content) for chapter in chapters] == [100, 100, 50]


def test_fallback_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError):
        split_text("甲。", length=0)
    with pytest.raises(ValueError):
        split_text("甲。", length=-5)


def test_all_empty_chapters_fall_back_to_length_split() -> None:
    """标题全在、正文全空 → 退回按长度切，别产出"一堆空章"。"""
    text = "第一章\n第二章\n第三章\n" + "正文正文。" * 300

    novel = parse_text(text, book_id="x", fallback_length=200)

    assert any(chapter.content for chapter in novel.chapters)


# ────────────────────────── 超长章节再切（PROMPT_DESIGN §3）──────────────────────────


def test_split_long_chapters_respects_max_length() -> None:
    chapters = [Chapter(index=0, title="第一章 长", content="句子。" * 500)]

    result = split_long_chapters(chapters, max_length=300)

    assert len(result) > 1
    assert all(len(chapter.content) <= 300 for chapter in result)


def test_split_long_chapters_keeps_short_chapters_untouched() -> None:
    chapters = [
        Chapter(index=0, title="第一章 短", content="很短。"),
        Chapter(index=1, title="第二章 也短", content="也很短。"),
    ]

    result = split_long_chapters(chapters, max_length=1000)

    assert result == chapters


def test_split_long_chapters_labels_parts_and_reindexes() -> None:
    chapters = [
        Chapter(index=0, title="第一章 短", content="很短。"),
        Chapter(index=1, title="第二章 长", content="句子。" * 500),
    ]

    result = split_long_chapters(chapters, max_length=300)

    assert result[0].title == "第一章 短"
    assert result[1].title.startswith("第二章 长（1/")
    assert [c.index for c in result] == list(range(len(result)))


def test_split_long_chapters_does_not_mutate_input() -> None:
    chapters = [Chapter(index=0, title="第一章 长", content="句子。" * 500)]
    snapshot = chapters[0].model_copy(deep=True)

    split_long_chapters(chapters, max_length=300)

    assert chapters == [snapshot]


def test_split_long_chapters_rejects_non_positive_max() -> None:
    with pytest.raises(ValueError):
        split_long_chapters([], max_length=0)


def test_parse_text_can_split_long_chapters_inline() -> None:
    text = _make_novel([("第一章 长", "句子。" * 500)])

    novel = parse_text(text, book_id="x", max_chapter_length=300)

    assert len(novel.chapters) > 1
    assert all(len(chapter.content) <= 300 for chapter in novel.chapters)
    assert all("（" in chapter.title for chapter in novel.chapters)


def test_novel_split_long_returns_new_object() -> None:
    novel = parse_text(_make_novel([("第一章 长", "句子。" * 500)]), book_id="x")

    split = novel.split_long(max_length=300)

    assert split is not novel
    assert len(novel.chapters) == 1
    assert len(split.chapters) > 1
