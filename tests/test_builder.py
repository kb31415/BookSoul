"""阶段 5 测试：角色卡组装 + 校验 + v2 导出（`MVP_PLAN.md` 阶段 5）。

全部纯本地，不调 API。
"""

from __future__ import annotations

import json

import pytest

from booksoul.assemble import (
    DEFAULT_TAG,
    MergedPersona,
    PersonaChange,
    PersonaIncrement,
    PersonaTimeline,
    Quote,
    RelationshipChange,
    TimelinePoint,
    build_card,
    build_description,
    build_relations,
    build_timeline,
    card_to_v2_payload,
    validate_card,
)
from booksoul.schema import CharacterCard


def make_merged(
    *,
    with_fields: bool = True,
    relationships: list[RelationshipChange] | None = None,
    points: list[TimelinePoint] | None = None,
) -> MergedPersona:
    persona = PersonaIncrement(
        personality="表面疏离寡言，对在意的人用行动而非言语表达" if with_fields else "",
        desire="想护她周全，却不肯承认这份在意" if with_fields else "",
        flaw="过度隐忍，宁可被误解也不解释" if with_fields else "",
        secret="他的真实身份是前朝遗孤" if with_fields else "",
        speech_style="话短、少解释，惯用否定句作答" if with_fields else "",
        relationships=relationships
        or [
            RelationshipChange(target="师妹", change="剑冢之后开始护着她"),
            RelationshipChange(target="师妹", change="为她挡下一剑"),
        ],
        quotes=[
            Quote(field="personality", text="沈知舟立在廊下"),
            Quote(field="desire", text="剑法第一"),
            Quote(field="flaw", text="不必问"),
            Quote(field="secret", text="前朝遗孤"),
            Quote(field="speech_style", text="不必问"),
        ],
        speech_samples=["不必问。", "手滑。"],
    )
    return MergedPersona(persona=persona, timeline=PersonaTimeline(points=points or []))


# ────────────────────────── description ──────────────────────────


def test_description_collects_text_fields() -> None:
    card = build_card("沈知舟", make_merged())[0]

    assert "性格：表面疏离寡言" in card.description
    assert "欲望：想护她周全" in card.description
    assert "缺陷：过度隐忍" in card.description
    assert "秘密：他的真实身份" in card.description
    assert "说话风格：话短" in card.description


def test_description_includes_source_book() -> None:
    description = build_description(make_merged().persona, source_book="青云旧事")

    assert "来源：《青云旧事》" in description


def test_description_skips_empty_fields() -> None:
    description = build_description(PersonaIncrement(personality="只有性格"))

    assert description == "性格：只有性格"


# ────────────────────────── relations（§6.3 单向 + §7 演化）──────────────────────────


def test_relations_group_evolution_by_target() -> None:
    """同一 target 的多次变化合成一条 Relation，全部进 evolution（§7）。"""
    relations = build_relations(make_merged().persona, character="沈知舟")

    assert len(relations) == 1
    assert relations[0].to == "师妹"
    assert relations[0].from_ == "沈知舟"
    assert relations[0].evolution == ["剑冢之后开始护着她", "为她挡下一剑"]


def test_relation_description_takes_latest_change() -> None:
    relations = build_relations(make_merged().persona, character="沈知舟")

    assert relations[0].description == "为她挡下一剑"


def test_relations_are_directional() -> None:
    """§6.3：单向存储，from 是主角自己。"""
    relations = build_relations(make_merged().persona, character="沈知舟")

    assert all(relation.from_ == "沈知舟" for relation in relations)


def test_relations_multiple_targets() -> None:
    persona = PersonaIncrement(
        relationships=[
            RelationshipChange(target="师妹", change="疏远"),
            RelationshipChange(target="掌门", change="敬而远之"),
        ]
    )

    relations = build_relations(persona, character="甲")

    assert {relation.to for relation in relations} == {"师妹", "掌门"}


def test_relations_empty() -> None:
    assert build_relations(PersonaIncrement(), character="甲") == []


# ────────────────────────── timeline ──────────────────────────


def test_timeline_converts_points_to_events() -> None:
    points = [
        TimelinePoint(chapter_index=0, field="personality", value="初现"),
        TimelinePoint(chapter_index=3, field="personality", value="转变", previous="初现"),
    ]

    events = build_timeline(MergedPersona(persona=PersonaIncrement(), timeline=PersonaTimeline(points=points)))

    assert len(events) == 2
    assert events[0].id == "t0"
    assert events[0].summary == "personality：初现"
    assert events[0].chapter == "第0章"
    assert events[1].summary == "personality：初现 → 转变"


def test_timeline_respects_limit() -> None:
    points = [TimelinePoint(chapter_index=i, field="flaw", value=f"v{i}") for i in range(500)]

    events = build_timeline(
        MergedPersona(persona=PersonaIncrement(), timeline=PersonaTimeline(points=points)),
        limit=200,
    )

    assert len(events) == 200


def test_timeline_empty() -> None:
    assert build_timeline(MergedPersona(persona=PersonaIncrement(), timeline=PersonaTimeline())) == []


# ────────────────────────── build_card ──────────────────────────


def test_build_card_fills_standard_fields() -> None:
    card, report = build_card(
        "沈知舟",
        make_merged(),
        book_id="青云旧事",
        source_book="青云旧事",
        first_mes="「站在那儿别动。」",
        mes_example="<START>\n{{user}}: 师兄\n{{char}}: 手滑。",
        tags=["古风"],
    )

    assert card.name == "沈知舟"
    assert card.personality.startswith("表面疏离")
    assert card.first_mes == "「站在那儿别动。」"
    assert "<START>" in card.mes_example
    assert DEFAULT_TAG in card.tags and "古风" in card.tags
    assert card.source_book == "青云旧事"
    assert report.character == "沈知舟"


def test_build_card_puts_quotes_into_extraction_meta() -> None:
    card, _ = build_card("沈知舟", make_merged())

    quotes = card.extraction_meta["quotes"]
    assert len(quotes) == 5
    assert {quote["field"] for quote in quotes} >= {"personality", "desire"}


def test_build_card_keeps_speech_style_and_samples() -> None:
    """`speech_style` 是实现期补进 §6.1 契约的字段（Prompt 3 明确要抽）。"""
    card, _ = build_card("沈知舟", make_merged())

    assert card.speech_style.startswith("话短")
    assert card.extraction_meta["speech_samples"] == ["不必问。", "手滑。"]


def test_build_card_records_changes() -> None:
    merged = make_merged()
    merged.persona.changes.append(
        PersonaChange(**{"field": "desire", "from": "X", "to": "Y", "reason": "r"})
    )

    card, _ = build_card("沈知舟", merged)

    assert card.extraction_meta["changes"][0]["from"] == "X"


def test_build_card_does_not_invent_scenario() -> None:
    """§6.1 的 scenario 是"开场情境"，我们没抽 —— 不许编。"""
    card, _ = build_card("沈知舟", make_merged())

    assert card.scenario == ""


def test_build_card_report_counts() -> None:
    _, report = build_card("沈知舟", make_merged())

    assert report.relation_count == 1
    assert report.relation_evolution_total == 2
    assert report.quote_count == 5
    assert report.speech_sample_count == 2
    assert report.ok is True


def test_build_report_as_dict_is_serialisable() -> None:
    _, report = build_card("沈知舟", make_merged())

    json.dumps(report.as_dict(), ensure_ascii=False)


# ────────────────────────── 校验 ──────────────────────────


def test_validate_flags_missing_required() -> None:
    card = CharacterCard(name="甲", description="", personality="")
    report = validate_card(card)

    assert "description" in report.missing_required
    assert "personality" in report.missing_required
    assert report.ok is False


def test_validate_warns_on_empty_optional_fields() -> None:
    card, report = build_card("沈知舟", make_merged(with_fields=False))

    assert any("desire 为空" in warning for warning in report.warnings)


def test_validate_warns_when_field_has_no_quote() -> None:
    card = CharacterCard(name="甲", description="乙", personality="有内容但没引用")

    report = validate_card(card)

    assert any("personality 没有对应的原文引用" in warning for warning in report.warnings)


def test_validate_accepts_quotes_tagged_with_chapter() -> None:
    """引用可能带 `@chN` 章节标记（§7 合并时加的），校验要认。"""
    card = CharacterCard(
        name="甲",
        description="乙",
        personality="有内容",
        extraction_meta={"quotes": [{"field": "personality@ch3", "text": "原文"}]},
    )

    report = validate_card(card)

    assert not any("没有对应的原文引用" in warning for warning in report.warnings)


def test_validate_checks_mes_example_format() -> None:
    card = CharacterCard(name="甲", description="乙", personality="丙", mes_example="没有分隔符也没有占位符")

    report = validate_card(card)

    assert any("<START>" in warning for warning in report.warnings)
    assert any("{{user}}" in warning for warning in report.warnings)


def test_validate_warns_on_odd_first_mes_length() -> None:
    card = CharacterCard(name="甲", description="乙", personality="丙", first_mes="太短")

    report = validate_card(card)

    assert any("first_mes 长度" in warning for warning in report.warnings)


def test_validate_passes_clean_card() -> None:
    card, report = build_card(
        "沈知舟",
        make_merged(),
        first_mes="「站在那儿别动。」他没回头，剑尖挑开雨幕，声音冷得像雨。",
        mes_example="<START>\n{{user}}: 师兄\n{{char}}: 手滑。",
    )

    assert report.ok is True
    assert report.missing_required == []


# ────────────────────────── v2 导出 ──────────────────────────


def test_v2_export_has_required_spec_fields() -> None:
    card, _ = build_card("沈知舟", make_merged())
    payload = card_to_v2_payload(card)

    assert payload["spec"] == "chara_card_v2"
    assert payload["spec_version"] == "2.0"
    assert set(payload["data"]) >= {
        "name", "description", "personality", "scenario", "first_mes", "mes_example",
        "creator_notes", "tags", "character_version", "character_book", "extensions",
    }


def test_v2_export_puts_speech_style_in_extensions() -> None:
    card, _ = build_card("沈知舟", make_merged())

    booksoul = card_to_v2_payload(card)["data"]["extensions"]["booksoul"]

    assert booksoul["speech_style"].startswith("话短")
    assert "relations" in booksoul and "timeline" in booksoul
    assert "extraction_meta" in booksoul


def test_v2_export_round_trips() -> None:
    """导出的 v2 能原样导回来（阶段 5 验收：产物可被格式校验器接受）。"""
    card, _ = build_card("沈知舟", make_merged(), first_mes="开场白", mes_example="<START>\n{{user}}: 甲")
    payload = card_to_v2_payload(card)

    restored = CharacterCard.from_tavern(payload)

    assert restored.to_tavern_v2() == payload
    assert restored.speech_style == card.speech_style
    assert restored.relations == card.relations


def test_v2_export_is_json_serialisable() -> None:
    card, _ = build_card("沈知舟", make_merged())

    text = json.dumps(card_to_v2_payload(card), ensure_ascii=False)

    assert "沈知舟" in text
    assert "speech_style" in text
