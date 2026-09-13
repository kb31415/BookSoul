"""阶段 2 验收测试（`MVP_PLAN.md` 阶段 2「验收标准」）。

两条验收标准，逐条落成自动化断言：

1. **对一篇真实短篇，切出的章节数与目测一致**
   —— `conftest.REAL_STORY_CHAPTERS` 定义了几章，就必须切出几章，
   而且每章字数能按「一句 × 重复次数」精确核对。
2. **GBK 文件能正确读出**
   —— 同一篇语料分别以 utf-8 与 gbk 落盘，解析结果必须一致、无乱码。

另外把 `PROMPT_DESIGN.md` §3 对阶段 2 的下游约束（单章 ≤6000 字）也验一遍。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from booksoul.config import Settings
from booksoul.ingest import (
    DEFAULT_MAX_CHAPTER_LENGTH,
    dump_novel,
    from_novel_record,
    parse_file,
    to_novel_record,
)
from booksoul.storage import build_repositories
from conftest import REAL_STORY_CHAPTERS, REAL_STORY_NOISE


def _write_story(tmp_path: Path, text: str, name: str, encoding: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


# ────────────────────── 验收 ①：章节数与目测一致 ──────────────────────


def test_chapter_count_matches_manual_count(tmp_path: Path, real_story_text: str) -> None:
    """目测 = `REAL_STORY_CHAPTERS` 的条数（6 章）→ 切出来必须也是这个数。"""
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")

    novel = parse_file(path)

    assert len(novel.chapters) == len(REAL_STORY_CHAPTERS)


def test_every_chapter_title_matches_the_source(tmp_path: Path, real_story_text: str) -> None:
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")

    novel = parse_file(path)

    assert [c.title for c in novel.chapters] == [title for title, _, _ in REAL_STORY_CHAPTERS]


def test_every_chapter_body_length_is_exact(tmp_path: Path, real_story_text: str) -> None:
    """每章字数 = len(一句) × 重复次数 —— 逐章精确核对，确保没切多也没切少。"""
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")

    novel = parse_file(path)

    for chapter, (_, line, repeat) in zip(novel.chapters, REAL_STORY_CHAPTERS):
        assert len(chapter.content) == len(line) * repeat, chapter.title


def test_noise_is_stripped_from_every_chapter(tmp_path: Path, real_story_text: str) -> None:
    """盗版站噪声不能留在正文里（阶段 3 会把正文直接喂给 LLM，噪声就是白花钱）。"""
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")

    novel = parse_file(path)
    joined = "\n".join(chapter.content for chapter in novel.chapters)

    for noise_line in REAL_STORY_NOISE.strip().split("\n"):
        assert noise_line not in joined


def test_head_noise_never_becomes_a_chapter(tmp_path: Path, real_story_text: str) -> None:
    """封面 / 作者 / 广告行不能被当成一章（那样章号会整体错位）。"""
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")

    novel = parse_file(path)

    assert novel.chapters[0].title == REAL_STORY_CHAPTERS[0][0]
    assert "作者" not in novel.chapters[0].content
    assert "example-ad.com" not in "".join(c.content for c in novel.chapters)


# ────────────────────── 验收 ②：GBK 文件能正确读出 ──────────────────────


def test_gbk_file_is_decoded_without_mojibake(tmp_path: Path, real_story_text: str) -> None:
    path = _write_story(tmp_path, real_story_text, "青云旧事_gbk.txt", "gbk")

    novel = parse_file(path)
    joined = "\n".join(chapter.content for chapter in novel.chapters)

    # 关键中文必须原样出现
    for keyword in ("剑冢", "沈知舟", "月白长衫", "师妹", "掌门"):
        assert keyword in joined, keyword
    # 典型乱码特征一个都不能有
    for garbage in ("锟斤拷", "烫烫", "\ufffd", "ï¿½"):
        assert garbage not in joined, garbage


def test_utf8_and_gbk_parse_identically(tmp_path: Path, real_story_text: str) -> None:
    """同一篇文本的两种编码，解析结果必须逐字段一致（只有 encoding 允许不同）。"""
    utf8 = parse_file(_write_story(tmp_path, real_story_text, "a.txt", "utf-8"))
    gbk = parse_file(_write_story(tmp_path, real_story_text, "b.txt", "gbk"))

    assert gbk.chapters == utf8.chapters
    assert gbk.char_count == utf8.char_count
    assert gbk.encoding != utf8.encoding


def test_gbk_with_bom_less_gb18030_only_chars(tmp_path: Path) -> None:
    """gb18030 比 gbk 宽（含生僻字 / 全角符号），也要能读。"""
    text = "第一章 生僻\n" + "龘靐齉爩。" * 20 + "\n"
    path = tmp_path / "wide.txt"
    path.write_bytes(text.encode("gb18030"))

    novel = parse_file(path)

    assert "龘靐齉爩" in novel.chapters[0].content


# ────────────────────── 下游约束：单章 ≤6000 字 ──────────────────────


def test_long_chapter_is_split_for_prompt_design(tmp_path: Path) -> None:
    """`PROMPT_DESIGN.md` §3：单章超 6000 字要再切，否则 prompt 装不下。"""
    long_chapter = "这是一句很长的正文。" * 1200  # ≈ 12000 字
    path = tmp_path / "long.txt"
    path.write_text(f"第一章 超长\n{long_chapter}\n", encoding="utf-8")

    novel = parse_file(path, max_chapter_length=DEFAULT_MAX_CHAPTER_LENGTH)

    assert len(novel.chapters) > 1
    assert all(len(c.content) <= DEFAULT_MAX_CHAPTER_LENGTH for c in novel.chapters)
    assert novel.chapters[0].title.startswith("第一章 超长（1/")


def test_parsed_novel_round_trips_through_storage(tmp_path: Path, real_story_text: str) -> None:
    """解析结果经 Repository 落盘后要能原样读回（阶段 3 直接读它）。

    注意 `dump_novel()` 接收的是 **Repository 而不是目录** ——
    结构红线 ① 不允许业务代码自己碰文件路径。
    """
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")
    novel = parse_file(path)
    repos = build_repositories(Settings(data_dir=tmp_path / "store"))

    saved_id = dump_novel(novel, repos.novels)

    assert saved_id == novel.book_id
    assert (repos.novels.root / f"{novel.book_id}.json").is_file()

    record = repos.novels.load(novel.book_id)
    assert record is not None
    assert record.chapter_count == len(novel.chapters)
    assert from_novel_record(record) == novel


def test_parse_output_is_a_storage_record(tmp_path: Path, real_story_text: str) -> None:
    """两层模型要对得上：`to_novel_record()` 不能丢字段。"""
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")
    novel = parse_file(path)

    record = to_novel_record(novel)

    assert record.book_id == novel.book_id
    assert record.encoding == novel.encoding
    assert record.char_count == novel.char_count
    assert record.chapter_count == len(novel.chapters)
    assert from_novel_record(record) == novel


def test_book_id_comes_from_filename(tmp_path: Path, real_story_text: str) -> None:
    """`book_id` 从文件名来 —— 阶段 3 的 `data/cards/{book_id}/` 依赖它。"""
    path = _write_story(tmp_path, real_story_text, "青云旧事.txt", "utf-8")

    novel = parse_file(path)

    assert novel.book_id == "青云旧事"
    assert novel.source_path == str(path)


def test_optional_real_corpus_check() -> None:
    """`data/novels/*.txt` 里有真实小说就顺手解析并打印统计（人工目测用）。

    没有语料时跳过 —— 上面几条用例已经用构造语料把验收标准覆盖住了，
    这条是给"想放一本真书看看"留的入口。
    """
    novels_dir = Path(__file__).resolve().parents[1] / "data" / "novels"
    candidates = sorted(p for p in novels_dir.glob("*.txt") if p.is_file())
    if not candidates:
        pytest.skip("data/novels/ 下没有 .txt 语料，跳过真实语料自检")

    for path in candidates:
        novel = parse_file(path, max_chapter_length=DEFAULT_MAX_CHAPTER_LENGTH)
        titles = [chapter.title for chapter in novel.chapters]
        print(
            f"\n[真实语料] {path.name}: encoding={novel.encoding} "
            f"chars={novel.char_count} chapters={len(novel.chapters)}"
        )
        for chapter in novel.chapters[:12]:
            print(f"  #{chapter.index} {chapter.title} ({len(chapter.content)} 字)")

        assert novel.chapters, f"{path.name} 没切出任何章节"
        assert len(set(titles)) == len(titles), f"{path.name} 出现重复章节标题"
