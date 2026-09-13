"""角色识别测试（`MVP_PLAN.md` 阶段 3 任务 4：**mock LLM 响应**，测聚合与排序逻辑）。

全部使用假 `completion_fn`：不联网、不花钱、结果确定。
真实调用的验收（前 5 名确实是主要角色）见 `test_identify_real.py`，
需要显式打开开关才会跑。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from booksoul.extract import (
    ALIAS_SYSTEM_PROMPT,
    DEFAULT_CHAPTER_CHAR_LIMIT,
    DEFAULT_TOP_N,
    NAME_SYSTEM_PROMPT,
    build_alias_prompt,
    build_name_prompt,
    chapter_cache_fingerprint,
    identify_candidates,
    identify_chapters,
    merge_aliases,
    parse_alias_groups,
    parse_names,
    rank_candidates,
    sample_contexts,
    scan_chapter,
)
from booksoul.ingest import Chapter, Novel
from booksoul.llm import LLMCallError, LLMClient
from booksoul.storage import ChapterNameCache
from test_llm_client import fake_response


# ────────────────────────── 夹具与辅助 ──────────────────────────


def make_client(responses: list[Any], **overrides: Any) -> LLMClient:
    """按顺序吐出预设响应的假客户端（用完后重复最后一个）。"""
    queue = list(responses)

    def fake(**_: Any) -> Any:
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    defaults: dict[str, Any] = {
        "api_key": "sk-fake",
        "model": "deepseek/deepseek-chat",
        "completion_fn": fake,
        "sleep_fn": lambda _: None,
    }
    defaults.update(overrides)
    return LLMClient(**defaults)


def names_response(*names: str) -> Any:
    return fake_response(json.dumps({"characters": list(names)}, ensure_ascii=False))


def make_chapter(index: int, title: str, content: str) -> Chapter:
    return Chapter(index=index, title=title, content=content)


def make_novel(chapters: list[tuple[str, str]], book_id: str = "青云旧事") -> Novel:
    return Novel(
        book_id=book_id,
        encoding="utf-8",
        char_count=sum(len(content) for _, content in chapters),
        chapters=[
            make_chapter(index, title, content)
            for index, (title, content) in enumerate(chapters)
        ],
    )


# ────────────────────────── Prompt 组装（照搬设计）──────────────────────────


def test_name_prompt_contains_chapter_title_and_text() -> None:
    prompt = build_name_prompt("第一章 雨夜", "沈知舟立在廊下。")

    assert "【章节标题】第一章 雨夜" in prompt
    assert "【章节文本】" in prompt
    assert "沈知舟立在廊下。" in prompt


def test_name_prompt_handles_missing_title() -> None:
    assert "（无标题）" in build_name_prompt("", "正文")


def test_alias_prompt_wraps_contexts() -> None:
    assert build_alias_prompt("- 沈知舟：①「甲」") == "【候选名称及上下文】\n- 沈知舟：①「甲」"


def test_system_prompts_match_prompt_design() -> None:
    """提示词是设计定稿的原文，实现方不得改写（HANDOFF.md §0）。"""
    assert NAME_SYSTEM_PROMPT.startswith("你是中文小说人物识别专家。")
    assert '不提取泛称：如"众人""路人"' in NAME_SYSTEM_PROMPT
    assert '{"characters": ["沈知舟", "沈师兄", "林晚", "王婆婆"]}' in NAME_SYSTEM_PROMPT

    assert ALIAS_SYSTEM_PROMPT.startswith("你是中文小说人物分析专家。")
    assert "宁可漏合并，不可错合并" in ALIAS_SYSTEM_PROMPT
    assert '"main": "沈知舟", "aliases": ["沈师兄", "知舟"]' in ALIAS_SYSTEM_PROMPT


# ────────────────────────── 输出解析 ──────────────────────────


def test_parse_names_standard_shape() -> None:
    assert parse_names({"characters": ["沈知舟", "林晚"]}) == ["沈知舟", "林晚"]


def test_parse_names_deduplicates_preserving_order() -> None:
    assert parse_names({"characters": ["甲", "乙", "甲"]}) == ["甲", "乙"]


def test_parse_names_tolerates_other_shapes() -> None:
    assert parse_names(["甲", "乙"]) == ["甲", "乙"]
    assert parse_names({"names": ["甲"]}) == ["甲"]
    assert parse_names({"characters": "甲"}) == []
    assert parse_names(None) == []


def test_parse_names_strips_wrapping_symbols() -> None:
    """LLM 常给名字套引号 / 书名号。"""
    assert parse_names({"characters": ["「沈知舟」", "《林晚》", " 王婆婆 "]}) == [
        "沈知舟",
        "林晚",
        "王婆婆",
    ]


def test_parse_names_drops_empty_entries() -> None:
    assert parse_names({"characters": ["甲", "", "   ", None]}) == ["甲"]


def test_parse_alias_groups_standard() -> None:
    groups = parse_alias_groups(
        {
            "groups": [
                {"main": "沈知舟", "aliases": ["沈师兄", "知舟"]},
                {"main": "林晚", "aliases": [], "reason": "第7章"},
            ]
        }
    )

    assert [g.main for g in groups] == ["沈知舟", "林晚"]
    assert groups[0].aliases == ["沈师兄", "知舟"]
    assert groups[1].reason == "第7章"


def test_parse_alias_groups_drops_main_from_aliases() -> None:
    groups = parse_alias_groups({"groups": [{"main": "甲", "aliases": ["甲", "乙"]}]})

    assert groups[0].aliases == ["乙"]


def test_parse_alias_groups_skips_group_without_main() -> None:
    assert parse_alias_groups({"groups": [{"aliases": ["乙"]}]}) == []


def test_parse_alias_groups_tolerates_garbage() -> None:
    assert parse_alias_groups("不是 JSON 结构") == []
    assert parse_alias_groups({"groups": None}) == []
    assert parse_alias_groups({"groups": [None, "x", 3]}) == []


# ────────────────────────── 逐章识别与缓存 ──────────────────────────


def test_scan_chapter_sends_title_and_text() -> None:
    captured: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return names_response("沈知舟")

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    chapter = make_chapter(0, "第一章 雨夜", "沈知舟立在廊下。")

    cache = scan_chapter(client, chapter)

    assert captured[0]["messages"][0]["content"] == NAME_SYSTEM_PROMPT
    assert "第一章 雨夜" in captured[0]["messages"][1]["content"]
    assert "沈知舟立在廊下。" in captured[0]["messages"][1]["content"]
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert cache.chapter_index == 0
    assert cache.names == ["沈知舟"]


def test_single_chunk_cache_parts_is_flat() -> None:
    """只有一块时 parts 压平成名字列表（文件更好读）。"""
    client = make_client([names_response("甲", "乙")])

    cache = scan_chapter(client, make_chapter(0, "第一章", "正文"))

    assert cache.parts == ["甲", "乙"]
    assert cache.chunks == [["甲", "乙"]]


def test_long_chapter_is_split_into_chunks() -> None:
    """`PROMPT_DESIGN.md` §3：单章超过 6000 字要再切块，且按块分别调用。"""
    body = "沈知舟。" * 400  # 1600 字
    calls = {"count": 0}

    def fake(**_: Any) -> Any:
        calls["count"] += 1
        return names_response("沈知舟")

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    chapter = make_chapter(0, "第一章", body)

    cache = scan_chapter(client, chapter, char_limit=500)

    assert calls["count"] > 1, "超长章节应该分多次调用"
    assert len(cache.chunks) == calls["count"]
    assert cache.names == ["沈知舟"]


def test_identify_chapters_uses_cache_on_second_run(repos) -> None:
    """同内容重跑不重复调用 LLM（`MVP_PLAN.md` §1：避免重复烧 token）。"""
    calls = {"count": 0}

    def fake(**_: Any) -> Any:
        calls["count"] += 1
        return names_response("沈知舟", "林晚")

    novel = make_novel([("第一章 甲", "沈知舟与林晚。"), ("第二章 乙", "沈知舟独自。")])

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    _, first = identify_chapters(novel, client, repos)
    calls_after_first = calls["count"]

    client2 = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    caches, second = identify_chapters(novel, client2, repos)

    assert calls_after_first == 2
    assert calls["count"] == 2, "第二次不该再调用 LLM"
    assert second.chapter_cached == 2
    assert second.chapter_scanned == 0
    assert first.chapter_scanned == 2
    assert len(caches) == 2


def test_identify_chapters_rescans_when_text_changes(repos) -> None:
    def fake(**_: Any) -> Any:
        return names_response("沈知舟")

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    identify_chapters(make_novel([("第一章", "原文。")]), client, repos)

    client2 = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    _, report = identify_chapters(make_novel([("第一章", "改过的正文。")]), client2, repos)

    assert report.chapter_scanned == 1
    assert report.chapter_cached == 0


def test_identify_chapters_force_ignores_cache(repos) -> None:
    def fake(**_: Any) -> Any:
        return names_response("沈知舟")

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    novel = make_novel([("第一章", "正文。")])
    identify_chapters(novel, client, repos)

    client2 = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    _, report = identify_chapters(novel, client2, repos, force=True)

    assert report.chapter_scanned == 1
    assert report.chapter_cached == 0


def test_chapter_cache_files_are_named_by_chapter_index(repos) -> None:
    """`PROMPT_DESIGN.md` §3：`{book_id}/chapters/{i}.names.json`。"""
    client = make_client([names_response("甲")])
    novel = make_novel([("第一章", "正文一。"), ("第二章", "正文二。")])

    identify_chapters(novel, client, repos)

    chapters_dir = repos.cards.character_dir("青云旧事") / "chapters"
    assert (chapters_dir / "0.names.json").is_file()
    assert (chapters_dir / "1.names.json").is_file()


def test_chapter_cache_round_trips_through_repository(repos) -> None:
    client = make_client([names_response("甲", "乙")])
    novel = make_novel([("第一章", "正文。")])
    identify_chapters(novel, client, repos)

    loaded = repos.cards.load_chapter_names("青云旧事", 0)

    assert loaded is not None
    assert loaded.names == ["甲", "乙"]


def test_all_chapters_fail_raises_not_silently(repos) -> None:
    """LLM 一直失败必须报错退出（全局约定：不静默吞掉）。"""

    def fail(**_: Any) -> Any:
        raise RuntimeError("API 挂了")

    client = LLMClient(
        api_key="sk", model="m", completion_fn=fail, sleep_fn=lambda _: None, max_retries=0
    )

    with pytest.raises(LLMCallError):
        identify_chapters(make_novel([("第一章", "正文。")]), client, repos)


# ────────────────────────── 汇总与排序（§3 实现要点）──────────────────────────


def test_rank_sorts_by_chapter_count_then_mentions() -> None:
    """排序键：出现章数↓ → 出现次数↓ → 名字（稳定）。"""
    caches = [
        ChapterNameCache.from_chunks(0, [["甲", "乙", "乙", "丙"]], "f0"),
        ChapterNameCache.from_chunks(1, [["甲", "乙", "丁"]], "f1"),
        ChapterNameCache.from_chunks(2, [["甲", "丙"]], "f2"),
    ]
    chapters = [make_chapter(i, f"第{i}章", "") for i in range(3)]

    ranked = rank_candidates(caches, chapters)

    assert [c.name for c in ranked] == ["甲", "乙", "丙", "丁"]
    assert ranked[0].chapter_count == 3
    assert ranked[0].mentions == 3
    assert ranked[1].chapter_count == 2
    assert ranked[1].mentions == 3
    assert ranked[2].chapter_count == 2
    assert ranked[2].mentions == 2


def test_rank_counts_mentions_within_chapter() -> None:
    caches = [ChapterNameCache.from_chunks(0, [["甲", "甲", "甲"]], "f")]
    ranked = rank_candidates(caches, [make_chapter(0, "第一章", "")])

    assert ranked[0].mentions == 3
    assert ranked[0].chapter_count == 1


def test_rank_counts_mentions_across_chunks_of_one_chapter() -> None:
    """一章多块时，同章内跨块的出现次数要累加（章数仍算 1）。"""
    caches = [ChapterNameCache.from_chunks(0, [["甲"], ["甲", "甲"]], "f")]

    ranked = rank_candidates(caches, [make_chapter(0, "第一章", "")])

    assert ranked[0].mentions == 3
    assert ranked[0].chapter_count == 1


def test_rank_respects_top_n() -> None:
    caches = [ChapterNameCache.from_chunks(0, [[f"角色{i}" for i in range(30)]], "f")]
    ranked = rank_candidates(caches, [make_chapter(0, "第一章", "")], top_n=5)

    assert len(ranked) == 5
    assert DEFAULT_TOP_N == 15


def test_rank_is_stable_for_equal_scores() -> None:
    """并列时按名字排序 —— 同样输入永远同样输出。"""
    caches = [ChapterNameCache.from_chunks(0, [["乙", "甲", "丙"]], "f")]
    chapters = [make_chapter(0, "第一章", "")]

    assert [c.name for c in rank_candidates(caches, chapters)] == ["丙", "乙", "甲"]
    assert [c.name for c in rank_candidates(caches, chapters)] == ["丙", "乙", "甲"]


def test_rank_records_chapter_indexes() -> None:
    caches = [
        ChapterNameCache.from_chunks(0, [["甲"]], "f0"),
        ChapterNameCache.from_chunks(2, [["甲"]], "f2"),
    ]
    chapters = [make_chapter(i, f"第{i}章", "") for i in range(3)]

    ranked = rank_candidates(caches, chapters)

    assert ranked[0].chapter_indexes == [0, 2]


def test_rank_empty_caches() -> None:
    assert rank_candidates([], []) == []


# ────────────────────────── 上下文抽样（§4：判断的唯一依据）──────────────────────────


def test_sample_contexts_returns_sentences_with_name() -> None:
    chapter = make_chapter(
        0,
        "第一章",
        "沈知舟立在廊下，一言不发。林晚撑伞走近。沈师兄，你今日不去么？",
    )

    contexts = sample_contexts("沈知舟", [chapter])

    assert contexts
    assert all("沈知舟" in sentence for sentence in contexts)


def test_sample_contexts_limits_count() -> None:
    chapter = make_chapter(0, "第一章", "甲在此。" * 10)

    assert len(sample_contexts("甲", [chapter], limit=2)) == 2


def test_sample_contexts_crosses_chapters_in_order() -> None:
    chapters = [
        make_chapter(0, "第一章", "甲在此。"),
        make_chapter(1, "第二章", "甲又来了。"),
    ]

    contexts = sample_contexts("甲", chapters, limit=2)

    assert contexts == ["甲在此。", "甲又来了。"]


def test_sample_contexts_empty_when_absent() -> None:
    assert sample_contexts("不存在", [make_chapter(0, "第一章", "甲在此。")]) == []


# ────────────────────────── 主流程与落盘 ──────────────────────────


def test_identify_candidates_writes_outputs(repos) -> None:
    client = make_client([names_response("沈知舟", "林晚")])
    novel = make_novel([("第一章 雨夜", "沈知舟与林晚。")])

    payload, report = identify_candidates(novel, client, repos, top_n=10)

    assert payload.book_id == "青云旧事"
    # 两人都出现 1 次、都在同一章 → 并列，按名字排序（林 U+6797 < 沈 U+6C88）
    assert payload.names() == ["林晚", "沈知舟"]
    assert report.candidate_count == 2
    assert (repos.cards.character_dir("青云旧事") / "candidates.json").is_file()
    assert (repos.cards.character_dir("青云旧事") / "name_contexts.json").is_file()


def test_candidates_json_is_reloadable(repos) -> None:
    client = make_client([names_response("甲")])
    novel = make_novel([("第一章", "甲在此。")])
    identify_candidates(novel, client, repos)

    raw = repos.cards.load_json("青云旧事", "candidates")

    assert raw is not None
    assert raw["book_id"] == "青云旧事"
    assert raw["candidates"][0]["name"] == "甲"
    assert "usage" in raw


def test_identify_candidates_records_usage(repos) -> None:
    client = make_client([names_response("甲"), names_response("乙")])
    novel = make_novel([("第一章", "甲。"), ("第二章", "乙。")])

    payload, report = identify_candidates(novel, client, repos)

    assert payload.usage["calls"] == 2
    assert payload.usage["prompt_tokens"] == 200  # fake_response 固定 100/次
    assert report.usage["calls"] == 2


def test_identify_candidates_ranks_multi_chapter_first(repos) -> None:
    """构造数据让"主角"出现章数最多 —— 排序必须把它放第一。"""
    caches = [
        ChapterNameCache.from_chunks(0, [["主角", "龙套"]], "f0"),
        ChapterNameCache.from_chunks(1, [["主角"]], "f1"),
        ChapterNameCache.from_chunks(2, [["主角"]], "f2"),
    ]
    chapters = [make_chapter(i, f"第{i}章", "") for i in range(3)]

    ranked = rank_candidates(caches, chapters)

    assert [c.name for c in ranked] == ["主角", "龙套"]
    assert ranked[0].chapter_count == 3


# ────────────────────────── 别名归并（Prompt 2）──────────────────────────


def test_merge_aliases_returns_groups() -> None:
    client = make_client(
        [
            fake_response(
                json.dumps(
                    {
                        "groups": [
                            {"main": "沈知舟", "aliases": ["沈师兄", "知舟"]},
                            {"main": "林晚", "aliases": []},
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )

    groups = merge_aliases(
        client,
        {"沈知舟": ["沈知舟立在廊下。"], "沈师兄": ["沈师兄，你今日不去么？"]},
    )

    assert len(groups) == 1, "只保留真正发生合并的组"
    assert groups[0].main == "沈知舟"
    assert groups[0].aliases == ["沈师兄", "知舟"]


def test_merge_aliases_builds_context_block() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response('{"groups": []}')

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    merge_aliases(client, {"沈知舟": ["沈知舟立在廊下。", "沈知舟摇头。"]})

    user = captured["messages"][1]["content"]
    assert captured["messages"][0]["content"] == ALIAS_SYSTEM_PROMPT
    assert "【候选名称及上下文】" in user
    assert "- 沈知舟：" in user
    assert "①沈知舟立在廊下。" in user
    assert "②沈知舟摇头。" in user


def test_merge_aliases_empty_input_makes_no_call() -> None:
    calls = {"count": 0}

    def fake(**_: Any) -> Any:
        calls["count"] += 1
        return fake_response("{}")

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)

    assert merge_aliases(client, {}) == []
    assert calls["count"] == 0


def test_merge_aliases_handles_unparsable_output() -> None:
    client = make_client([fake_response("模型没按格式输出")])

    assert merge_aliases(client, {"甲": ["甲在此。"]}) == []


# ────────────────────────── 缓存指纹 ──────────────────────────


def test_fingerprint_changes_with_content() -> None:
    chapter = make_chapter(0, "第一章", "原文")

    assert chapter_cache_fingerprint(chapter, ["原文"]) != chapter_cache_fingerprint(
        chapter, ["改过"]
    )


def test_fingerprint_changes_with_title() -> None:
    first = make_chapter(0, "第一章", "正文")
    second = make_chapter(0, "第一章 改", "正文")

    assert chapter_cache_fingerprint(first, ["正文"]) != chapter_cache_fingerprint(
        second, ["正文"]
    )


def test_fingerprint_is_stable() -> None:
    chapter = make_chapter(0, "第一章", "正文")

    assert chapter_cache_fingerprint(chapter, ["正文"]) == chapter_cache_fingerprint(
        chapter, ["正文"]
    )


# ────────────────────────── 边界 ──────────────────────────


def test_identify_empty_novel(repos) -> None:
    client = make_client([names_response()])
    novel = Novel(book_id="空书")

    payload, report = identify_candidates(novel, client, repos)

    assert payload.candidates == []
    assert report.chapter_total == 0


def test_scan_chapter_rejects_non_positive_limit() -> None:
    client = make_client([names_response()])

    with pytest.raises(ValueError):
        scan_chapter(client, make_chapter(0, "第一章", "正文"), char_limit=0)


def test_default_char_limit_matches_prompt_design() -> None:
    assert DEFAULT_CHAPTER_CHAR_LIMIT == 6000
