"""阶段 4 抽取流程测试（`MVP_PLAN.md` 阶段 4 + `PROMPT_DESIGN.md` §5/§6）。

全部用假 `completion_fn`：不联网、不花钱、结果确定。
真实调用的验收（"抽出的字段像书中人"）见 `test_extract_real.py`，默认跳过。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from booksoul.assemble import PersonaIncrement, Quote, RelationshipChange
from booksoul.extract import (
    ExtractionReport,
    batch_passages,
    build_persona_prompt,
    empty_persona_summary,
    extract_character,
    extract_chapter_increments,
    extract_persona_for_chapter,
    format_persona_summary,
    generate_card_fields,
    parse_card_fields,
    parse_persona_increment,
    retrieve_passages,
    score_passages,
)
from booksoul.ingest import Chapter, Novel
from booksoul.llm import LLMClient
from test_llm_client import fake_response


# ────────────────────────── 夹具 ──────────────────────────


def make_client(payloads: list[Any], **overrides: Any) -> LLMClient:
    """按顺序吐出预设 JSON（不是字符串）的假客户端。

    `retries` 不是 `LLMClient` 的参数（那是抽取函数的参数），所以这里挑出去，
    避免 `LLMClient(**defaults)` 报 unexpected keyword。
    """
    overrides.pop("retries", None)
    queue = [
        item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        for item in payloads
    ]

    def fake(**_: Any) -> Any:
        if len(queue) > 1:
            return fake_response(queue.pop(0))
        return fake_response(queue[0])

    defaults: dict[str, Any] = {
        "api_key": "sk-fake",
        "model": "deepseek/deepseek-chat",
        "completion_fn": fake,
        "sleep_fn": lambda _: None,
    }
    defaults.update(overrides)
    return LLMClient(**defaults)


def make_chapter(index: int, content: str, title: str = "第一章") -> Chapter:
    return Chapter(index=index, title=title, content=content)


def make_novel(chapters: list[tuple[str, str]], book_id: str = "青云旧事") -> Novel:
    return Novel(
        book_id=book_id,
        encoding="utf-8",
        char_count=sum(len(content) for _, content in chapters),
        chapters=[
            Chapter(index=index, title=title, content=content)
            for index, (title, content) in enumerate(chapters)
        ],
    )


#: 一段"真实感"的章节文本（用空行分段，方便测检索）。
CHAPTER_TEXT = (
    "沈知舟立在廊下，一言不发。\n\n"
    "林晚撑伞走近，轻声道：「沈师兄，你今日不去么？」\n\n"
    "远处的王婆婆叹了口气，众人皆知沈师兄剑法第一。\n\n"
    "「不必问。」他转身走进雨里。"
)

GENERIC_PERSONA = {
    "personality": "表面疏离寡言，对在意的人用行动而非言语表达",
    "desire": "想护她周全，却不肯承认这份在意",
    "flaw": "过度隐忍，宁可被误解也不解释",
    "secret": "",
    "speech_style": "话短、少解释，惯用否定句作答",
    "relationships": [{"target": "师妹", "change": "剑冢之后开始护着她"}],
    "quotes": [
        {"field": "personality", "text": "沈知舟立在廊下", "confidence": "explicit"},
        {"field": "desire", "text": "剑法第一", "confidence": "inferred"},
        {"field": "flaw", "text": "不必问", "confidence": "explicit"},
        {"field": "speech_style", "text": "不必问", "confidence": "explicit"},
    ],
    "speech_samples": ["不必问。"],
    "changes": [],
}


# ────────────────────────── 检索相关段落（§5 变量表）──────────────────────────


def test_retrieve_keeps_only_relevant_paragraphs() -> None:
    passages = retrieve_passages(make_chapter(0, CHAPTER_TEXT), ["沈知舟"])

    assert passages
    assert all("沈知舟" in passage for passage in passages)


def test_retrieve_splits_coarse_paragraphs() -> None:
    """分段粒度太粗时必须退到单换行切（真踩过的坑）。

    古籍 / 部分 txt 用**单换行**分段，一章可能只有两三段、单段几千字。若按空行切，
    "命中 1 段"就等于把整章（实测 4583 字）喂给模型 —— 里面所有人物都在，
    模型会把别人的事安到目标角色头上（《宛如约》第 0 章就是这么串的）。

    修好之后：命中 25 段共 906 字，人设才抽得准。
    """
    line = "沈知舟立於廊下，一言不發，雨水順著劍脊滑落。"  # 22 字
    body = "\n".join(line for _ in range(200))  # 单段约 4600 字，与真实语料同量级
    chapter = make_chapter(0, body)

    passages = retrieve_passages(chapter, ["沈知舟"])

    assert len(passages) == 200
    assert max(len(p) for p in passages) == len(line)


def test_split_paragraphs_keeps_blank_line_splitting_when_fine() -> None:
    """空行分段本来就细时，不要退到单换行（现代网文的空行是语义边界）。"""
    from booksoul.extract import split_paragraphs

    content = "甲。\n\n乙。\n丙。"  # 两块：["甲。", "乙。\n丙。"]

    assert split_paragraphs(content) == ["甲。", "乙。\n丙。"]


def test_split_paragraphs_empty_content() -> None:
    from booksoul.extract import split_paragraphs

    assert split_paragraphs("") == []
    assert split_paragraphs("\n\n\n") == []


def test_retrieve_uses_aliases_too() -> None:
    """§5：**必须用别名一起检索**，否则会漏掉大量段落。"""
    without_alias = retrieve_passages(make_chapter(0, CHAPTER_TEXT), ["沈知舟"])
    with_alias = retrieve_passages(make_chapter(0, CHAPTER_TEXT), ["沈知舟", "沈师兄"])

    assert len(with_alias) >= len(without_alias)
    assert any("沈师兄" in passage and "沈知舟" not in passage for passage in with_alias)


def test_retrieve_drops_chapter_title_line() -> None:
    """标题已单独作为 `{chapter_title}` 传入，不该重复出现在段落里。"""
    chapter = make_chapter(0, "第一章\n沈知舟立在廊下。", title="第一章")

    passages = retrieve_passages(chapter, ["沈知舟"])

    assert passages == ["沈知舟立在廊下。"]


def test_retrieve_can_keep_title_line() -> None:
    """标题与正文分成两段时，`include_title_line=True` 会把它一起带上。"""
    chapter = make_chapter(0, "第一章\n\n沈知舟立在廊下。", title="第一章")

    passages = retrieve_passages(chapter, ["沈知舟", "第一章"], include_title_line=True)

    assert passages == ["第一章", "沈知舟立在廊下。"]


def test_retrieve_empty_when_absent() -> None:
    assert retrieve_passages(make_chapter(0, CHAPTER_TEXT), ["不存在的人"]) == []


def test_retrieve_without_names_returns_nothing() -> None:
    assert retrieve_passages(make_chapter(0, CHAPTER_TEXT), []) == []


def test_score_passages_ignores_empty_names() -> None:
    assert score_passages(["甲在此。"], ["", None]) == []  # type: ignore[list-item]


# ────────────────────────── 分批（§5：单次 ≤5000 字）──────────────────────────


def test_batch_respects_max_chars() -> None:
    passages = [f"第{i}段。" * 20 for i in range(20)]  # 每段 80 字

    batches = batch_passages(passages, max_chars=200)

    assert len(batches) > 1
    assert all(len(batch) <= 200 for batch in batches)


def test_batch_keeps_all_content() -> None:
    passages = ["甲" * 40, "乙" * 40, "丙" * 40]

    batches = batch_passages(passages, max_chars=100)

    joined = "".join(batches).replace("\n", "")
    assert joined.count("甲") == 40
    assert joined.count("乙") == 40
    assert joined.count("丙") == 40


def test_batch_splits_oversized_single_passage() -> None:
    """单段就超限时硬切，不丢内容。"""
    batches = batch_passages(["甲" * 250], max_chars=100)

    assert all(len(batch) <= 100 for batch in batches)
    assert sum(len(batch) for batch in batches) == 250


def test_batch_empty_input() -> None:
    assert batch_passages([]) == []


def test_batch_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        batch_passages(["甲"], max_chars=0)


def test_batch_under_limit_is_single_batch() -> None:
    assert len(batch_passages(["甲。", "乙。"], max_chars=1000)) == 1


# ────────────────────────── Prompt 3 组装 ──────────────────────────


def test_persona_prompt_contains_all_variables() -> None:
    prompt = build_persona_prompt(
        character_name="沈知舟",
        aliases=["沈师兄", "知舟"],
        chapter_index=3,
        chapter_title="雨夜",
        existing_persona="- 性格：冷峻",
        relevant_passages="沈知舟立在廊下。",
    )

    assert "【角色】沈知舟" in prompt
    assert "沈师兄、知舟" in prompt
    assert "第 3 章 雨夜" in prompt
    assert "- 性格：冷峻" in prompt
    assert "沈知舟立在廊下。" in prompt


def test_persona_prompt_defaults_for_first_chapter() -> None:
    """§5 模板："如果是第一次抽取，此处写（无，这是首次抽取）"。"""
    prompt = build_persona_prompt("甲", [], 0, "第一章", "", "正文")

    assert empty_persona_summary() == "（无，这是首次抽取）"
    assert "（无，这是首次抽取）" in prompt
    assert "（无已知别名）" in prompt


def test_persona_prompt_keeps_output_format() -> None:
    """输出格式的 JSON 示例必须完整保留（模板变量替换不能吃掉花括号）。"""
    prompt = build_persona_prompt("甲", [], 0, "第一章", "", "正文")

    assert '"speech_style": ""' in prompt
    assert '"confidence": "explicit"' in prompt
    assert "【质量约束】" in prompt


def test_format_persona_summary_lists_filled_fields() -> None:
    summary = format_persona_summary(
        {"personality": "冷峻", "desire": "", "flaw": "隐忍"},
        relationships=[],
        changes=[],
    )

    assert "性格：冷峻" in summary
    assert "缺陷：隐忍" in summary
    assert "欲望" not in summary


def test_format_persona_summary_includes_relationships_and_changes() -> None:
    from booksoul.assemble import PersonaChange, RelationshipChange

    summary = format_persona_summary(
        {"personality": "冷峻"},
        relationships=[RelationshipChange(target="师妹", change="开始护着她")],
        changes=[PersonaChange(**{"field": "desire", "from": "X", "to": "Y", "reason": "r"})],
    )

    assert "与「师妹」：开始护着她" in summary
    assert "变化（desire）：X → Y" in summary


# ────────────────────────── 输出解析 ──────────────────────────


def test_parse_persona_increment_full_payload() -> None:
    increment = parse_persona_increment(GENERIC_PERSONA)

    assert increment.personality.startswith("表面疏离")
    assert increment.relationships[0].target == "师妹"
    assert len(increment.quotes) == 4
    assert increment.speech_samples == ["不必问。"]


def test_parse_persona_increment_tolerates_string_samples() -> None:
    increment = parse_persona_increment({"speech_samples": "只有一句。"})

    assert increment.speech_samples == ["只有一句。"]


def test_parse_persona_increment_tolerates_dict_relationships() -> None:
    increment = parse_persona_increment(
        {"relationships": {"target": "师妹", "change": "疏远"}}
    )

    assert increment.relationships[0].target == "师妹"


def test_parse_persona_increment_tolerates_string_relationships() -> None:
    increment = parse_persona_increment({"relationships": "和师妹关系变差"})

    assert increment.relationships[0].change == "和师妹关系变差"


def test_parse_persona_increment_ignores_unknown_keys() -> None:
    increment = parse_persona_increment({"personality": "冷峻", "unknown_field": 1})

    assert increment.personality == "冷峻"


def test_parse_persona_increment_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        parse_persona_increment(["不是 dict"])


def test_persona_change_from_key() -> None:
    increment = parse_persona_increment(
        {"changes": [{"field": "desire", "from": "X", "to": "Y", "reason": "r"}]}
    )

    assert increment.changes[0].from_ == "X"


# ────────────────────────── 单章抽取 ──────────────────────────


def test_extract_persona_for_chapter_calls_once_when_under_limit() -> None:
    calls = {"n": 0}

    def fake(**_: Any) -> Any:
        calls["n"] += 1
        return fake_response(json.dumps(GENERIC_PERSONA, ensure_ascii=False))

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)

    outcome = extract_persona_for_chapter(
        client, "沈知舟", ["沈师兄"], make_chapter(0, CHAPTER_TEXT)
    )

    assert calls["n"] == 1
    assert outcome.increment.personality.startswith("表面疏离")


def test_extract_persona_for_chapter_batches_long_input() -> None:
    """§5：命中段落超 5000 字要分批调用再合并。"""
    calls = {"n": 0}

    def fake(**_: Any) -> Any:
        calls["n"] += 1
        return fake_response(json.dumps({"speech_samples": [f"第{calls['n']}句"]}, ensure_ascii=False))

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    chapter = make_chapter(0, "沈知舟在此。" * 300)  # 1800 字

    outcome = extract_persona_for_chapter(
        client, "沈知舟", [], chapter, max_passage_chars=500
    )

    assert calls["n"] > 1
    assert len(outcome.increment.speech_samples) == calls["n"]


def test_extract_persona_for_chapter_no_passages_makes_no_call() -> None:
    calls = {"n": 0}

    def fake(**_: Any) -> Any:
        calls["n"] += 1
        return fake_response("{}")

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)

    outcome = extract_persona_for_chapter(client, "不存在", [], make_chapter(0, CHAPTER_TEXT))

    assert calls["n"] == 0
    assert outcome.increment.is_empty()


def test_extract_persona_for_chapter_validates_quotes() -> None:
    payload = {
        "personality": "表面疏离寡言，对在意的人用行动而非言语表达",
        "quotes": [{"field": "personality", "text": "原文里根本没有这句"}],
    }
    client = make_client([payload])

    outcome = extract_persona_for_chapter(client, "沈知舟", [], make_chapter(0, CHAPTER_TEXT))

    assert outcome.report.dropped_quotes
    assert outcome.report.ungrounded_fields == ["personality"]


# ────────────────────────── 逐章抽取（含"已有画像"回喂）──────────────────────────


def test_extract_chapter_increments_returns_one_per_chapter() -> None:
    client = make_client([GENERIC_PERSONA])
    novel = make_novel([("第一章", CHAPTER_TEXT), ("第二章", "别的内容。")])

    increments, report = extract_chapter_increments(novel, client, "沈知舟", ["沈师兄"])

    assert len(increments) == 2  # 与章节一一对应（没命中的是空增量）
    assert report.chapter_total == 2
    assert report.chapter_hit == 1
    assert report.chapter_empty == 1


def test_extract_chapter_increments_feeds_existing_persona_forward() -> None:
    """§5 逐章增量：已有画像要喂回给下一章。"""
    prompts: list[str] = []

    def fake(**kwargs: Any) -> Any:
        prompts.append(kwargs["messages"][1]["content"])
        return fake_response(json.dumps(GENERIC_PERSONA, ensure_ascii=False))

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    novel = make_novel([("第一章", CHAPTER_TEXT), ("第二章", CHAPTER_TEXT)])

    extract_chapter_increments(novel, client, "沈知舟", ["沈师兄"])

    assert len(prompts) == 2
    assert "（无，这是首次抽取）" in prompts[0]
    assert "表面疏离寡言" in prompts[1], "第二章应带上第一章抽到的画像"


def test_extract_chapter_increments_records_usage() -> None:
    client = make_client([GENERIC_PERSONA])

    _, report = extract_chapter_increments(
        make_novel([("第一章", CHAPTER_TEXT)]), client, "沈知舟"
    )

    assert report.usage["calls"] == 1
    assert report.usage["prompt_tokens"] == 100


def test_extract_chapter_increments_reports_validation_problems() -> None:
    payload = {"personality": "无引用的一段性格描述文字", "quotes": []}
    client = make_client([payload])

    _, report = extract_chapter_increments(
        make_novel([("第一章", CHAPTER_TEXT)]), client, "沈知舟"
    )

    assert report.cleared_fields == ["personality"]
    assert report.has_problems is True


def test_report_fields_are_lists_not_counts() -> None:
    """报告字段必须是**列表**（人工把关要看具体是哪条），CLI 曾按列表用而崩。"""
    payload = {
        "personality": "表面疏离寡言，对在意的人用行动表达",
        "quotes": [{"field": "personality", "text": "原文里没有这句"}],
    }
    client = make_client([payload])

    _, report = extract_chapter_increments(
        make_novel([("第一章", CHAPTER_TEXT)]), client, "沈知舟"
    )

    assert isinstance(report.dropped_quotes, list)
    assert isinstance(report.cleared_fields, list)
    assert isinstance(report.ungrounded_fields, list)
    assert isinstance(report.cliches, list)
    assert isinstance(report.field_length_warnings, list)
    assert report.dropped_quotes  # 至少能看到被丢弃的原文片段


def test_extraction_report_as_dict_is_serialisable() -> None:
    import json

    report = ExtractionReport(character_name="沈知舟", chapter_hit=2)
    report.cleared_fields.append("desire")

    payload = report.as_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["cleared_fields"] == ["desire"]
    assert payload["chapter_hit"] == 2


def test_extract_chapter_increments_raises_after_retries() -> None:
    """Pydantic 校验/解析连续失败 → 报错退出，**绝不静默产出残缺 persona**。"""
    client = make_client(["这不是 JSON"], retries=0)

    with pytest.raises(RuntimeError) as excinfo:
        extract_chapter_increments(
            make_novel([("第一章", CHAPTER_TEXT)]), client, "沈知舟", retries=1
        )

    assert "第 0 章" in str(excinfo.value)


def test_extract_chapter_increments_retries_then_succeeds() -> None:
    client = make_client(["垃圾输出", GENERIC_PERSONA], retries=1)

    increments, report = extract_chapter_increments(
        make_novel([("第一章", CHAPTER_TEXT)]), client, "沈知舟", retries=1
    )

    assert increments[0].personality.startswith("表面疏离")
    assert report.chapter_hit == 1


# ────────────────────────── extract_character（合并）──────────────────────────


def test_extract_character_merges_increments() -> None:
    payload_a = dict(GENERIC_PERSONA)
    payload_b = {
        "personality": "后来变得更愿意解释自己",
        "quotes": [{"field": "personality", "text": "不必问", "confidence": "explicit"}],
        "speech_samples": ["再多问一句。"],
    }
    client = make_client([payload_a, payload_b])
    novel = make_novel([("第一章", CHAPTER_TEXT), ("第二章", CHAPTER_TEXT)])

    merged, report = extract_character(novel, client, "沈知舟", ["沈师兄"])

    assert merged.persona.personality == "后来变得更愿意解释自己"
    assert len(merged.persona.speech_samples) == 2
    assert merged.timeline.points
    assert report.chapter_hit == 2


def test_extract_character_conflict_goes_into_changes() -> None:
    client = make_client(
        [
            {"personality": "早期：冷硬寡言不爱解释", "quotes": [{"field": "personality", "text": "不必问"}]},
            {"personality": "后期：开始主动解释自己", "quotes": [{"field": "personality", "text": "不必问"}]},
        ]
    )
    novel = make_novel([("第一章", CHAPTER_TEXT), ("第二章", CHAPTER_TEXT)])

    merged, _ = extract_character(novel, client, "沈知舟")

    assert merged.persona.changes
    assert merged.field_evolution("personality").startswith("早期：")


# ────────────────────────── Prompt 4 ──────────────────────────


def test_generate_card_fields_returns_first_mes_and_example() -> None:
    client = make_client(
        [{"first_mes": "「站在那儿别动。」", "mes_example": "<START>\n{{user}}: 师兄\n{{char}}: 手滑。"}]
    )

    fields = generate_card_fields(client, "沈知舟", PersonaIncrement(**GENERIC_PERSONA))

    assert fields.first_mes == "「站在那儿别动。」"
    assert "<START>" in fields.mes_example
    assert "{{user}}" in fields.mes_example


def test_generate_card_fields_uses_speech_samples() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response(json.dumps({"first_mes": "A", "mes_example": "B"}))

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    generate_card_fields(client, "沈知舟", PersonaIncrement(speech_samples=["不必问。", "手滑。"]))

    prompt = captured["messages"][1]["content"]
    assert "不必问。" in prompt
    assert "手滑。" in prompt
    assert "你是角色卡设计师。" in prompt


def test_generate_card_fields_handles_no_samples() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response(json.dumps({"first_mes": "A", "mes_example": "B"}))

    client = LLMClient(api_key="sk", model="m", completion_fn=fake, sleep_fn=lambda _: None)
    generate_card_fields(client, "甲", PersonaIncrement())

    assert "书中没有可用的原话素材" in captured["messages"][1]["content"]


def test_generate_card_fields_raises_after_retries() -> None:
    client = make_client(["不是 JSON"])

    with pytest.raises(RuntimeError):
        generate_card_fields(client, "沈知舟", PersonaIncrement(), retries=0)


def test_parse_card_fields_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        parse_card_fields("不是 dict")


def test_parse_card_fields_tolerates_missing_keys() -> None:
    fields = parse_card_fields({"first_mes": "只有开场白"})

    assert fields.first_mes == "只有开场白"
    assert fields.mes_example == ""


# ────────────────────────── 边界 ──────────────────────────


def test_extract_on_empty_novel() -> None:
    client = make_client([{}])
    novel = Novel(book_id="空书")

    increments, report = extract_chapter_increments(novel, client, "甲")

    assert increments == []
    assert report.chapter_total == 0


def test_increment_is_empty_only_looks_at_text_fields() -> None:
    """`is_empty()` 的语义：只看**五个文本字段 + relationships**。

    它决定的是"这一章对画像有没有贡献"，而不是"这一章一个字符都没抽到" ——
    只有 quotes / speech_samples 的增量在合并时仍会被保留（§7 要求"全部保留"），
    但不该被当成"抽到了人设"。
    """
    assert PersonaIncrement().is_empty() is True
    assert PersonaIncrement(personality="有内容").is_empty() is False
    assert PersonaIncrement(quotes=[Quote(text="引用")]).is_empty() is True
    assert PersonaIncrement(speech_samples=["原话"]).is_empty() is True
    assert PersonaIncrement(changes=[]).is_empty() is True
    assert PersonaIncrement(relationships=[RelationshipChange(target="甲", change="疏远")]).is_empty() is False
