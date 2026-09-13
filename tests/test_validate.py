"""质量校验测试（`PROMPT_DESIGN.md` §8 的检查清单，逐项覆盖）。

§8 的「引用真实性」是最有价值的一项 —— 它把"反幻觉"从口号变成可执行校验，
所以这里单独测一组。
"""

from __future__ import annotations

import pytest

from booksoul.assemble import (
    CLICHE_BLACKLIST,
    PersonaIncrement,
    Quote,
    RelationshipChange,
    ValidationReport,
    clean_for_matching,
    find_cliches,
    quote_is_grounded,
    validate_persona_increment,
)

SOURCE = (
    "沈知舟立在廊下，一言不发。\n"
    "「站在那儿别动。」他没回头。\n"
    "众人皆知沈师兄剑法第一。"
)


def inc(**kwargs) -> PersonaIncrement:
    return PersonaIncrement(**kwargs)


# ────────────────── 引用真实性（§8 最有价值的一项）──────────────────


def test_grounded_quote_matches_source() -> None:
    assert quote_is_grounded("沈知舟立在廊下", SOURCE) is True


def test_grounding_ignores_punctuation() -> None:
    """§8：允许忽略标点。"""
    assert quote_is_grounded("「站在那儿别动。」", SOURCE) is True
    assert quote_is_grounded("站在那儿别动", SOURCE) is True


def test_fabricated_quote_is_not_grounded() -> None:
    assert quote_is_grounded("他说他要走了。", SOURCE) is False


def test_empty_quote_is_not_grounded() -> None:
    assert quote_is_grounded("", SOURCE) is False
    assert quote_is_grounded("   ", SOURCE) is False


def test_clean_for_matching_strips_punct_and_space() -> None:
    assert clean_for_matching("「沈知舟，立 在廊下。」") == "沈知舟立在廊下"


def test_validate_drops_fabricated_quotes() -> None:
    """§8：引用对不上原文 → 丢弃该条 quote 并告警。"""
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动而非言语表达",
        quotes=[
            Quote(field="personality", text="沈知舟立在廊下"),
            Quote(field="personality", text="他其实很热情"),
        ],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert len(increment.quotes) == 1
    assert increment.quotes[0].text == "沈知舟立在廊下"
    assert len(report.dropped_quotes) == 1
    assert "他其实很热情" in report.dropped_quotes[0]


def test_validate_clears_fields_without_any_quotes() -> None:
    """§8 引用完整性：每个非空字段都必须有对应 quotes，否则置空。"""
    increment = inc(personality="无依据的性格", desire="")

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality == ""
    assert report.cleared_fields == ["personality"]


def test_validate_keeps_fields_with_valid_quotes() -> None:
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动表达",
        quotes=[Quote(field="personality", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality.startswith("表面疏离")
    assert report.cleared_fields == []
    assert report.ok


def test_validate_clears_fields_without_matching_quote_entry() -> None:
    """§8 引用完整性：字段**没有**对应的 quotes 条目 → 置空（哪怕别的字段有引用）。"""
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动表达",
        desire="想护她周全",
        quotes=[Quote(field="personality", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality != ""  # 有对应引用，保留
    assert increment.desire == ""  # 没有对应引用条目 → 置空
    assert report.cleared_fields == ["desire"]


def test_validate_marks_fields_whose_quote_was_fabricated() -> None:
    """字段**有**条目但引用被证伪 → 非严格模式保留 + 标记（交人工把关，这是阶段 4 的把关点）。"""
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动表达",
        quotes=[Quote(field="personality", text="他其实很热情")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality != ""  # 保留
    assert report.ungrounded_fields == ["personality"]
    assert report.dropped_quotes  # 假引用被丢弃


def test_strict_mode_clears_ungrounded_fields() -> None:
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动表达",
        quotes=[Quote(field="personality", text="他其实很热情")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE, strict=True)

    assert increment.personality == ""
    assert "personality" in report.cleared_fields


def test_quote_field_key_must_match_field_name() -> None:
    """引用的 `field` 指到别的字段 → 本字段等于没有对应引用条目 → 置空。"""
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动表达",
        quotes=[Quote(field="desire", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality == ""
    assert report.cleared_fields == ["personality"]


# ────────────────── 套话检测（§8）──────────────────


@pytest.mark.parametrize(
    "cliche",
    ["性格复杂", "内心矛盾", "很有魅力", "多面性", "深不可测"],
)
def test_cliches_are_detected(cliche: str) -> None:
    increment = inc(personality=f"他{cliche}，让人看不透")

    hits = find_cliches(increment)

    assert hits and cliche in hits[0]


def test_cliches_do_not_clear_the_field_only_warn() -> None:
    """§8：套话只告警、人工复核 —— 不做自动置空。"""
    increment = inc(
        personality="他的性格复杂，既有温柔的一面也有冷漠的一面",
        quotes=[Quote(field="personality", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality != ""
    assert report.cliches
    assert report.ok is False


def test_cliches_in_relationships_are_detected() -> None:
    increment = inc(relationships=[RelationshipChange(target="师妹", change="关系很有魅力")])

    assert find_cliches(increment)


def test_blacklist_covers_design_examples() -> None:
    for term in ("性格复杂", "内心矛盾", "很有魅力", "多面性"):
        assert term in CLICHE_BLACKLIST


def test_concrete_wording_is_not_a_cliche() -> None:
    increment = inc(personality="表面疏离寡言，对在意的人用行动而非言语表达")

    assert find_cliches(increment) == []


# ────────────────── 长度约束（§8）──────────────────


def test_too_long_field_warns() -> None:
    increment = inc(
        personality="很长" * 30,
        quotes=[Quote(field="personality", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert report.field_length_warnings
    assert "personality" in report.field_length_warnings[0]
    assert increment.personality != ""  # 只告警，不置空


def test_too_short_field_warns() -> None:
    increment = inc(desire="想走", quotes=[Quote(field="desire", text="沈知舟立在廊下")])

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert any("desire" in warning for warning in report.field_length_warnings)


def test_in_range_field_does_not_warn() -> None:
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动而非言语表达",  # 21 字
        quotes=[Quote(field="personality", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert report.field_length_warnings == []


def test_empty_fields_are_not_length_checked() -> None:
    report = validate_persona_increment(inc(), source_text=SOURCE)

    assert report.field_length_warnings == []
    assert report.cleared_fields == []


# ────────────────── 报告本身 ──────────────────


def test_report_ok_for_clean_increment() -> None:
    increment = inc(
        personality="表面疏离寡言，对在意的人用行动而非言语表达",
        quotes=[Quote(field="personality", text="沈知舟立在廊下")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert report.ok is True
    assert report.summary() == "通过"


def test_report_summary_mentions_problems() -> None:
    increment = inc(
        personality="无依据的性格",
        quotes=[Quote(field="personality", text="编造的一句")],
    )

    report = validate_persona_increment(increment, source_text=SOURCE)

    assert "丢弃" in report.summary()
    assert report.ok is False


def test_report_merge_combines_batches() -> None:
    first = ValidationReport(chapter_index=3, dropped_quotes=["a"])
    second = ValidationReport(chapter_index=3, cliches=["b"], cleared_fields=["c"])

    merged = ValidationReport.merge([first, second])

    assert merged.chapter_index == 3
    assert merged.dropped_quotes == ["a"]
    assert merged.cliches == ["b"]
    assert merged.cleared_fields == ["c"]


def test_report_merge_empty() -> None:
    assert ValidationReport.merge([]).ok is True


def test_report_as_dict_is_serialisable() -> None:
    import json

    report = ValidationReport(chapter_index=1, cleared_fields=["desire"])

    json.dumps(report.as_dict(), ensure_ascii=False)


def test_validation_mutates_in_place() -> None:
    increment = inc(personality="无依据的性格")

    validate_persona_increment(increment, source_text=SOURCE)

    assert increment.personality == ""
