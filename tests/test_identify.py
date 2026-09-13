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
    DEFAULT_CHAPTER_CHAR_LIMIT,
    DEFAULT_TOP_N,
    Candidate,
    CharacterGroup,
    SAME_REFERENCE_MARKERS,
    apply_forbidden_merges,
    apply_same_reference_merges,
    build_alias_prompt,
    build_name_prompt,
    chapter_cache_fingerprint,
    collect_contexts,
    filter_merge_input,
    find_family_relations,
    find_same_reference_sentences,
    forbidden_merges,
    format_same_reference_evidence,
    identify_candidates,
    identify_chapters,
    merge_aliases,
    normalise_names,
    parse_alias_groups,
    parse_names,
    rank_candidates,
    resolve_source_form,
    same_reference_merges,
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
    prompt = build_alias_prompt("- 沈知舟：①「甲」")

    assert prompt.startswith("你是中文小说人物分析专家。")
    assert prompt.rstrip().endswith("【候选名称及上下文】\n- 沈知舟：①「甲」")


def test_system_prompts_match_prompt_design() -> None:
    """提示词来自 `prompts/*.md`（设计 §11：模板读文件，不硬编码），措辞逐字一致。

    阶段 3 原本把 Prompt 1/2 硬编码在代码里，后改为模板加载 —— 更贴合设计，
    也让"改 prompt 不用改代码"成立。
    """
    names = build_name_prompt("第一章", "正文")
    assert names.startswith("你是中文小说人物识别专家。")
    assert '不提取泛称：如"众人""路人"' in names
    assert '{"characters": ["沈知舟", "沈师兄", "林晚", "王婆婆"]}' in names

    aliases = build_alias_prompt("- 沈知舟：①「甲」")
    assert aliases.startswith("你是中文小说人物分析专家。")
    assert "宁可漏合并，不可错合并" in aliases
    assert '"main": "沈知舟", "aliases": ["沈师兄", "知舟"]' in aliases


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

    # Prompt 1 的模板把"你是……"和章节内容放在同一个文本里 →
    # system 消息为空，全部内容在 user 消息里
    content = captured[0]["messages"][1]["content"]
    assert content.startswith("你是中文小说人物识别专家。")
    assert "第一章 雨夜" in content
    assert "沈知舟立在廊下。" in content
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

    groups, notes = merge_aliases(
        client,
        {"沈知舟": ["沈知舟立在廊下。"], "沈师兄": ["沈师兄，你今日不去么？"]},
    )

    assert len(groups) == 1, "只保留真正发生合并的组"
    assert groups[0].main == "沈知舟"
    assert groups[0].aliases == ["沈师兄", "知舟"]
    assert notes == []


def test_merge_aliases_builds_context_block() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response('{"groups": []}')

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    # 至少两个候选才会真调用（P0-2 过滤后 <2 就直接返回）
    merge_aliases(
        client,
        {"沈知舟": ["沈知舟立在廊下。", "沈知舟摇头。"], "沈师兄": ["沈师兄，你今日不去么？"]},
    )

    user = captured["messages"][1]["content"]
    assert user.startswith("你是中文小说人物分析专家。")
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

    groups, notes = merge_aliases(client, {})
    assert groups == []
    assert notes == []
    assert calls["count"] == 0


def test_merge_aliases_handles_unparsable_output() -> None:
    client = make_client([fake_response("模型没按格式输出")])

    groups, _ = merge_aliases(client, {"甲": ["甲在此。"]})

    assert groups == []


# ────────────────── P0-2：上下文可用性守卫 ──────────────────


def test_merge_aliases_skips_candidates_without_contexts() -> None:
    """P0-2：`contexts` 为空的候选**不参与归并**（没依据就不要判）。

    实测锚点：繁简摇摆导致 `司空学士`（简体）在繁体原文里精确匹配 0 次 →
    contexts 为空。这类候选送进去只会让模型瞎猜。
    """
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response('{"groups": []}')

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    _, notes = merge_aliases(
        client,
        {
            "司空學士": ["司空學士的花園，十分齊整。"],
            "司空学士": [],  # 繁简摇摆产生的空上下文候选
            "温氏": [],  # 同理
            "趙白": ["趙白少年儒雅。"],  # 留一个有上下文的，保证过滤后仍 ≥2 个
        },
    )

    prompt = captured["messages"][1]["content"]
    assert "司空學士" in prompt
    assert "趙白" in prompt
    assert "司空学士" not in prompt
    assert "温氏" not in prompt
    assert len(notes) == 2
    assert any("司空学士" in note for note in notes)


def test_merge_aliases_returns_empty_when_under_two_candidates() -> None:
    """剔掉无上下文的之后不足两个 → 根本没法判归并，直接返回。"""
    calls = {"count": 0}

    def fake(**_: Any) -> Any:
        calls["count"] += 1
        return fake_response('{"groups": []}')

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)

    groups, notes = merge_aliases(client, {"甲": ["甲在此。"], "乙": []})

    assert (groups, notes) == ([], ["跳过无上下文的候选：乙"])
    assert calls["count"] == 0


def test_filter_merge_input_separates_empty_contexts() -> None:
    kept, dropped = filter_merge_input({"甲": ["句子"], "乙": [], "丙": ["句子"]})

    assert set(kept) == {"甲", "丙"}
    assert dropped == ["乙"]


# ────────────────── P0-3：关系句守卫（真实案例锚点）──────────────────

#: 《宛如约》里的原句 —— 这一句是整个 bug 的源头。
FATHER_SON_SENTENCE = (
    "原來司空學士這個大兒子叫做司空約，表字默愛。"
    "司空約道：「孩兒要說，父親大人又要責備孩兒狂妄。」"
)


def test_find_family_relations_detects_father_son() -> None:
    relations = find_family_relations(FATHER_SON_SENTENCE)

    assert relations, "应识别出「A 的大兒子叫做 B」这类关系句"
    flattened = [(a, kin, b) for a, kin, b in relations]
    assert any("司空學士" in a and "兒子" in kin and "司空約" in b for a, kin, b in flattened)


def test_forbidden_merges_blocks_father_and_son() -> None:
    """锚点回归：司空學士 与 司空約 **不得**被合并（真实语料里是父子）。"""
    chapters = [make_chapter(0, "第0章", FATHER_SON_SENTENCE)]

    forbidden = forbidden_merges(["司空學士", "司空約"], chapters)

    assert frozenset({"司空學士", "司空約"}) in forbidden
    assert "兒子" in forbidden[frozenset({"司空學士", "司空約"})]


def test_forbidden_merges_empty_for_unrelated_names() -> None:
    chapters = [make_chapter(0, "第0章", "沈知舟立在廊下，一言不发。林晚撑伞走近。")]

    assert forbidden_merges(["沈知舟", "林晚"], chapters) == {}


def test_forbidden_merges_requires_both_names_present() -> None:
    chapters = [make_chapter(0, "第0章", FATHER_SON_SENTENCE)]

    # 只给一个名字，凑不成对
    assert forbidden_merges(["司空學士"], chapters) == {}
    assert forbidden_merges([], chapters) == {}


def test_forbidden_merges_detects_simplified_relation_too() -> None:
    """简体关系句同样要认（LLM 逐章可能在繁简之间摇摆）。"""
    chapters = [make_chapter(0, "第0章", "林晚的父親名叫林正，一向严厉。")]

    forbidden = forbidden_merges(["林晚", "林正"], chapters)

    assert frozenset({"林晚", "林正"}) in forbidden


def test_apply_forbidden_merges_strips_only_the_violating_alias() -> None:
    """只摘掉违反守卫的别名，组内其余别名保留。"""
    groups = [
        CharacterGroup(main="司空約", aliases=["司空學士", "默愛"]),
    ]
    forbidden = {frozenset({"司空約", "司空學士"}): "司空學士…大兒子…司空約"}

    filtered, notes = apply_forbidden_merges(groups, forbidden)

    assert len(filtered) == 1
    assert filtered[0].aliases == ["默愛"]
    assert len(notes) == 1
    assert "司空學士" in notes[0]


def test_apply_forbidden_merges_drops_group_when_all_aliases_rejected() -> None:
    groups = [CharacterGroup(main="甲", aliases=["乙"])]

    filtered, notes = apply_forbidden_merges(groups, {frozenset({"甲", "乙"}): "证据"})

    assert filtered == []
    assert len(notes) == 1


def test_merge_aliases_applies_forbidden_guard() -> None:
    """端到端：LLM 说要合并父子，守卫必须拦下。"""
    client = make_client(
        [fake_response(json.dumps({"groups": [{"main": "司空約", "aliases": ["司空學士"]}]}, ensure_ascii=False))]
    )
    chapters = [make_chapter(0, "第0章", FATHER_SON_SENTENCE)]
    forbidden = forbidden_merges(["司空約", "司空學士"], chapters)

    groups, notes = merge_aliases(
        client,
        {"司空約": ["司空約道：「孩兒要說。」"], "司空學士": ["司空學士的花園十分齊整。"]},
        forbidden=forbidden,
    )

    assert groups == [], "父子不得合并"
    assert any("司空學士" in note for note in notes)


# ────────────────── P0-1：字形归一（繁简摇摆）──────────────────


def test_resolve_source_form_keeps_form_present_in_source() -> None:
    chapters = [make_chapter(0, "第0章", "司空學士的花園十分齊整。")]

    assert resolve_source_form("司空學士", chapters) == "司空學士"


def test_resolve_source_form_maps_simplified_to_traditional() -> None:
    """锚点回归：LLM 给简体「司空学士」，原文是繁体「司空學士」。"""
    chapters = [make_chapter(0, "第0章", "司空學士的花園十分齊整。")]

    assert resolve_source_form("司空学士", chapters) == "司空學士"


def test_resolve_source_form_maps_traditional_to_simplified_source() -> None:
    chapters = [make_chapter(0, "第0章", "赵如子在廊下。")]

    assert resolve_source_form("趙如子", chapters) == "赵如子"


def test_resolve_source_form_unknown_name_returns_as_is() -> None:
    chapters = [make_chapter(0, "第0章", "司空學士的花園。")]

    assert resolve_source_form("查无此人", chapters) == "查无此人"


def test_normalise_names_merges_variants_across_chapters() -> None:
    """锚点回归：第 0 章输出简体、第 1 章输出繁体 → 归一后是同一个名字。"""
    caches = [
        ChapterNameCache.from_chunks(0, [["司空学士", "赵如子"]], "f0"),
        ChapterNameCache.from_chunks(1, [["司空學士", "趙如子", "司空約"]], "f1"),
    ]
    chapters = [
        make_chapter(0, "第0章", "司空學士與趙如子在此。"),
        make_chapter(1, "第1章", "司空學士與趙如子、司空約在此。"),
    ]

    normalised, changed = normalise_names(caches, chapters)

    assert changed == {"司空学士": "司空學士", "赵如子": "趙如子"}
    assert normalised[0].names == ["司空學士", "趙如子"]
    assert normalised[1].names == ["司空學士", "趙如子", "司空約"]


def test_normalise_names_no_change_returns_same_objects() -> None:
    caches = [ChapterNameCache.from_chunks(0, [["沈知舟"]], "f0")]
    chapters = [make_chapter(0, "第0章", "沈知舟立在廊下。")]

    normalised, changed = normalise_names(caches, chapters)

    assert changed == {}
    assert normalised == caches


def test_normalise_names_accumulates_mentions_across_variants() -> None:
    """归一后统计要把两个写法的出现**合并计数**（这是 P0-1 的核心收益）。"""
    caches = [
        ChapterNameCache.from_chunks(0, [["司空学士"], ["司空学士"]], "f0"),
        ChapterNameCache.from_chunks(1, [["司空學士"]], "f1"),
    ]
    chapters = [
        make_chapter(0, "第0章", "司空學士的花園。"),
        make_chapter(1, "第1章", "司空學士的花園。"),
    ]

    normalised, _ = normalise_names(caches, chapters)
    ranked = rank_candidates(normalised, chapters)

    assert [c.name for c in ranked] == ["司空學士"]
    assert ranked[0].chapter_count == 2
    assert ranked[0].mentions == 3


def test_collect_contexts_normalises_and_resolves_contexts() -> None:
    """`collect_contexts` 要顺便把候选名校正成原文写法，并抽到非空上下文。"""
    from booksoul.extract import Candidate

    candidates = [Candidate(name="司空学士", chapter_count=1, mentions=1)]
    chapters = [make_chapter(0, "第0章", "司空學士的花園十分齊整。")]

    contexts = collect_contexts(candidates, chapters)

    assert candidates[0].name == "司空學士"
    assert contexts["司空學士"]
    assert all("司空學士" in sentence for sentence in contexts["司空學士"])


def test_simplify_only_replaces_known_characters() -> None:
    from booksoul.extract import simplify

    assert simplify("司空學士") == "司空学士"
    assert simplify("甲乙丙") == "甲乙丙"  # 没有繁简差异的字保持原样


# ────────────────── 同指证据（正向）：原文明说"这两个名字是同一个人" ──────────────────

#: 《宛如约》原文 —— 直接点破"趙如子就是趙白"（女扮男装的化名）。
SAME_PERSON_SENTENCE = "和詩之趙如子即是趙白。"
#: 另一处：自我介绍用化名与表字。
SAME_PERSON_SENTENCE_2 = "趙如子因打一恭道：「晚生趙白，賤字非玉。」"


def test_find_same_reference_detects_explicit_statement() -> None:
    found = find_same_reference_sentences(SAME_PERSON_SENTENCE)

    assert found
    assert any("趙如子" in a and "趙白" in b for a, _, b in found)


def test_same_reference_merges_pairs_candidates() -> None:
    """锚点回归：趙如子 / 趙白 必须被判定为**应当合并**（原文直接点破）。"""
    chapters = [make_chapter(0, "第0章", SAME_PERSON_SENTENCE)]

    evidence = same_reference_merges(["趙如子", "趙白"], chapters)

    assert frozenset({"趙如子", "趙白"}) in evidence
    assert "即是" in evidence[frozenset({"趙如子", "趙白"})]


def test_same_reference_ignores_non_candidate_fragments() -> None:
    """正则会在句子层面切出很多非人名片段，必须用候选名集合过滤掉。"""
    chapters = [make_chapter(0, "第0章", "水縣小蓬萊山中有叫做列眉村。")]

    assert same_reference_merges(["趙如子", "趙白"], chapters) == {}


def test_same_reference_skips_substring_pairs() -> None:
    """"如子" 是 "趙如子" 的子串 → 不算"两个名字同指"，跳过（否则造冗余对）。"""
    chapters = [make_chapter(0, "第0章", SAME_PERSON_SENTENCE)]

    evidence = same_reference_merges(["趙如子", "如子", "趙白"], chapters)

    assert frozenset({"趙如子", "趙白"}) in evidence
    assert frozenset({"如子", "趙白"}) not in evidence


def test_same_reference_requires_exact_candidate_name() -> None:
    chapters = [make_chapter(0, "第0章", SAME_PERSON_SENTENCE)]

    assert same_reference_merges(["查无此人"], chapters) == {}


def test_hetero_reference_wins_over_homo_reference() -> None:
    """异指优先：同一对被判为父子就不允许再按同指合并。"""
    chapters = [make_chapter(0, "第0章", "原來司空學士這個大兒子叫做司空約，即是默愛。")]

    forbidden = forbidden_merges(["司空學士", "司空約"], chapters)
    evidence = same_reference_merges(["司空學士", "司空約"], chapters, forbidden=forbidden)

    assert forbidden, "应识别为父子"
    assert frozenset({"司空學士", "司空約"}) not in evidence


def test_format_same_reference_evidence_block() -> None:
    """函数会把名字对**排序后**输出（`A = B` 的左右顺序按 Unicode 码点）。"""
    block = format_same_reference_evidence({frozenset({"甲", "乙"}): "甲即是乙"})

    assert "乙 = 甲" in block
    assert "甲即是乙" in block
    assert format_same_reference_evidence({}) == "（无）"


def test_alias_prompt_includes_same_reference_block() -> None:
    prompt = build_alias_prompt("- 甲：①句子", same_reference="- 甲 = 乙    原文依据：甲即是乙")

    assert "【同指强证据】" in prompt
    assert "甲 = 乙" in prompt
    assert "不要用\"出现次数多少\"作为判据" in prompt


def test_alias_prompt_defaults_same_reference_to_none() -> None:
    assert "（无）" in build_alias_prompt("- 甲：①句子")


def test_apply_same_reference_creates_group_when_none_exists() -> None:
    groups, notes = apply_same_reference_merges([], {frozenset({"趙如子", "趙白"}): "证据"})

    assert len(groups) == 1
    assert set(groups[0].all_names()) == {"趙如子", "趙白"}
    assert notes


def test_apply_same_reference_merges_two_groups() -> None:
    """LLM 把同一人判成两组 → 同指证据把两组并起来。"""
    groups = [
        CharacterGroup(main="趙如子", aliases=["如子"]),
        CharacterGroup(main="趙白", aliases=["趙非玉"]),
    ]

    merged, notes = apply_same_reference_merges(groups, {frozenset({"趙如子", "趙白"}): "证据"})

    assert len(merged) == 1
    assert merged[0].main in {"趙如子", "趙白"}
    assert set(merged[0].all_names()) == {"趙如子", "趙白", "如子", "趙非玉"}
    assert notes


def test_apply_same_reference_absorbs_short_forms() -> None:
    """"如子" ⊂ "趙如子" → 主名确定后把短写法一并收为别名。"""
    groups, _ = apply_same_reference_merges(
        [CharacterGroup(main="如子", aliases=[])],
        {frozenset({"趙如子", "趙白"}): "证据"},
    )

    names = set(groups[0].all_names())
    assert "如子" in names, "短写法要跟着并进来"
    assert {"趙如子", "趙白"} <= names


def test_apply_same_reference_keeps_existing_alias() -> None:
    groups = [CharacterGroup(main="趙如子", aliases=["晚生"], reason="原有")]
    groups, _ = apply_same_reference_merges(groups, {frozenset({"趙如子", "趙白"}): "证据"})

    assert groups[0].main == "趙如子"
    assert set(groups[0].aliases) == {"晚生", "趙白"}


def test_apply_same_reference_no_evidence_is_noop() -> None:
    groups = [CharacterGroup(main="甲", aliases=["乙"])]

    assert apply_same_reference_merges(groups, {}) == (groups, [])


def test_merge_aliases_forces_merge_from_evidence() -> None:
    """端到端：即使 LLM 什么都没合并，同指证据也要把该合的人合上。"""
    client = make_client([fake_response('{"groups": []}')])
    chapters = [make_chapter(0, "第0章", SAME_PERSON_SENTENCE)]
    evidence = same_reference_merges(["趙如子", "趙白"], chapters)

    groups, notes = merge_aliases(
        client,
        {"趙如子": ["趙如子道：「晚生趙白。」"], "趙白": ["趙白少年儒雅。"]},
        same_reference=evidence,
    )

    assert len(groups) == 1
    assert {"趙如子", "趙白"} <= set(groups[0].all_names())
    assert any("同指证据" in note for note in notes)


def test_merge_aliases_keeps_forbidden_priority_over_same_reference() -> None:
    """父子既被判异指，又有别处的"就是"句式 → 不能合并。"""
    client = make_client([fake_response('{"groups": []}')])
    chapters = [make_chapter(0, "第0章", "原來司空學士這個大兒子叫做司空約，即是默愛。")]
    forbidden = forbidden_merges(["司空學士", "司空約"], chapters)
    evidence = same_reference_merges(["司空學士", "司空約"], chapters, forbidden=forbidden)

    groups, _ = merge_aliases(
        client,
        {"司空學士": ["司空學士的花園。"], "司空約": ["司空約道：「孩兒要說。」"]},
        forbidden=forbidden,
        same_reference=evidence,
    )

    assert groups == [], "父子不得被合并"


def test_occurrence_count_is_not_a_merge_judgement() -> None:
    """确保"出现次数"没有掺进归并判据（真踩过：趙白 79 次 > 趙如子 34 次被当成反证）。

    这里构造两个次数悬殊但**有同指证据**的名字，必须合并。
    """
    client = make_client([fake_response('{"groups": []}')])
    chapters = [make_chapter(0, "第0章", SAME_PERSON_SENTENCE)]
    evidence = same_reference_merges(["趙如子", "趙白"], chapters)

    groups, _ = merge_aliases(
        client,
        {"趙白": ["趙白出現很多次。"], "趙如子": ["趙如子出現較少。"]},
        same_reference=evidence,
    )

    assert len(groups) == 1


def test_same_reference_marker_table_covers_design_examples() -> None:
    """标记词要覆盖"化名/本名/小名/表字/即是/自称"这几类。"""
    from booksoul.extract import SAME_REFERENCE_MARKERS

    for marker in ("即是", "就是", "自稱", "化名", "本名", "小名", "表字", "賤字"):
        assert marker in SAME_REFERENCE_MARKERS


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
