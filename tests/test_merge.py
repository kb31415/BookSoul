"""多章增量合并测试（`PROMPT_DESIGN.md` §7 的合并规则表，逐条覆盖）。"""

from __future__ import annotations

from booksoul.assemble import (
    PERSONA_TEXT_FIELDS,
    PersonaChange,
    PersonaIncrement,
    Quote,
    RelationshipChange,
    merge_persona_increments,
    relationships_by_target,
)

TEXT_FIELDS = PERSONA_TEXT_FIELDS


def inc(**kwargs) -> PersonaIncrement:
    return PersonaIncrement(**kwargs)


# ────────────────── 文本字段：非空覆盖空 → 较晚覆盖较早 + 记 changes ──────────────────


def test_empty_increment_is_skipped() -> None:
    merged = merge_persona_increments([inc(), inc(personality="冷峻"), inc()])

    assert merged.persona.personality == "冷峻"
    assert merged.persona.is_empty() is False


def test_non_empty_fills_empty_slot() -> None:
    merged = merge_persona_increments(
        [inc(personality="甲性格"), inc(desire="乙欲望")]
    )

    assert merged.persona.personality == "甲性格"
    assert merged.persona.desire == "乙欲望"


def test_later_value_wins_when_both_present() -> None:
    """§7：都有值时保留**较晚出现**的（后文更能体现最终人设）。"""
    merged = merge_persona_increments(
        [inc(personality="早期表述"), inc(personality="后期表述")]
    )

    assert merged.persona.personality == "后期表述"


def test_conflict_is_recorded_in_changes_not_silently_overwritten() -> None:
    """§7：若为冲突则记入 `changes` 并保留两者。"""
    merged = merge_persona_increments(
        [inc(personality="早期表述"), inc(personality="后期表述")]
    )

    assert len(merged.persona.changes) == 1
    change = merged.persona.changes[0]
    assert change.field == "personality"
    assert change.from_ == "早期表述"
    assert change.to == "后期表述"
    assert change.reason


def test_identical_values_do_not_create_changes() -> None:
    merged = merge_persona_increments(
        [inc(personality="一模一样的表述"), inc(personality="一模一样的表述")]
    )

    assert merged.persona.personality == "一模一样的表述"
    assert merged.persona.changes == []


def test_all_text_fields_merge_independently() -> None:
    merged = merge_persona_increments(
        [
            inc(personality="A1", desire="B1", flaw="C1", secret="D1", speech_style="E1"),
            inc(personality="A2", desire="B2", flaw="C2", secret="D2", speech_style="E2"),
        ]
    )

    for name in TEXT_FIELDS:
        assert getattr(merged.persona, name).endswith("2"), name
    assert {change.field for change in merged.persona.changes} == set(TEXT_FIELDS)


def test_blank_string_does_not_override() -> None:
    merged = merge_persona_increments([inc(personality="有值"), inc(personality="   ")])

    assert merged.persona.personality == "有值"


# ────────────────── relationships：按 target 聚合，全部保留 ──────────────────


def test_relationships_are_all_kept_in_order() -> None:
    """§7：同一 target 的多次变化**全部保留**，形成演化序列。"""
    merged = merge_persona_increments(
        [
            inc(relationships=[RelationshipChange(target="师妹", change="初遇时只当她是累赘")]),
            inc(relationships=[RelationshipChange(target="师妹", change="剑冢之后开始护着她")]),
        ]
    )

    assert [r.change for r in merged.persona.relationships] == [
        "初遇时只当她是累赘",
        "剑冢之后开始护着她",
    ]


def test_relationships_by_target_groups_evolution() -> None:
    merged = merge_persona_increments(
        [
            inc(relationships=[RelationshipChange(target="师妹", change="疏远")]),
            inc(
                relationships=[
                    RelationshipChange(target="师妹", change="护着她"),
                    RelationshipChange(target="掌门", change="敬而远之"),
                ]
            ),
        ]
    )

    grouped = relationships_by_target(merged.persona)

    assert grouped["师妹"] == ["疏远", "护着她"]
    assert grouped["掌门"] == ["敬而远之"]


def test_relationships_by_target_skips_empty_rows() -> None:
    assert relationships_by_target(inc(relationships=[RelationshipChange()])) == {}


# ────────────────── quotes：全部保留（去重），附章节号 ──────────────────


def test_quotes_keep_all_and_are_tagged_with_chapter() -> None:
    """§7：`quotes` 全部保留（去重），附上章节号。"""
    merged = merge_persona_increments(
        [
            inc(quotes=[Quote(field="personality", text="甲说。")]),
            inc(quotes=[Quote(field="desire", text="乙说。")]),
        ]
    )

    assert [(q.field, q.text) for q in merged.persona.quotes] == [
        ("personality", "甲说。"),  # 第 0 章不加后缀（ch0 是隐含默认）
        ("desire@ch1", "乙说。"),
    ]


def test_duplicate_quotes_are_deduped_across_chapters() -> None:
    merged = merge_persona_increments(
        [
            inc(quotes=[Quote(field="personality", text="同一句。")]),
            inc(quotes=[Quote(field="personality", text="同一句。")]),
        ]
    )

    assert len(merged.persona.quotes) == 1


def test_same_text_different_field_is_not_deduped() -> None:
    merged = merge_persona_increments(
        [
            inc(quotes=[Quote(field="personality", text="同一句。")]),
            inc(quotes=[Quote(field="desire", text="同一句。")]),
        ]
    )

    assert len(merged.persona.quotes) == 2


def test_quote_confidence_is_preserved() -> None:
    merged = merge_persona_increments(
        [inc(quotes=[Quote(field="flaw", text="推断句。", confidence="inferred")])]
    )

    assert merged.persona.quotes[0].confidence == "inferred"


# ────────────────── speech_samples / changes：全部保留 ──────────────────


def test_speech_samples_are_all_kept_and_deduped() -> None:
    """§7：`speech_samples` 全部保留，最终由 Prompt 4 挑选。"""
    merged = merge_persona_increments(
        [
            inc(speech_samples=["手滑。", "不必问。"]),
            inc(speech_samples=["不必问。", "再多问一句。"]),
        ]
    )

    assert merged.persona.speech_samples == ["手滑。", "不必问。", "再多问一句。"]


def test_upstream_changes_are_all_kept() -> None:
    """§7：`changes` 全部保留，是"成长弧光"的原始素材。"""
    merged = merge_persona_increments(
        [
            inc(changes=[PersonaChange(field="desire", **{"from": "X"}, to="Y", reason="r1")]),
            inc(changes=[PersonaChange(field="flaw", **{"from": "A"}, to="B", reason="r2")]),
        ]
    )

    fields = [change.field for change in merged.persona.changes]
    assert fields == ["desire", "flaw"]


def test_persona_change_from_alias_round_trips() -> None:
    change = PersonaChange(**{"field": "desire", "from": "想要自由", "to": "想要留下"})

    assert change.from_ == "想要自由"
    assert change.model_dump(by_alias=True)["from"] == "想要自由"


# ────────────────── 时间线（§7 的"副作用"）──────────────────


def test_timeline_records_first_appearance() -> None:
    merged = merge_persona_increments([inc(personality="初现"), inc()])

    assert [p.field for p in merged.timeline.points] == ["personality"]
    assert merged.timeline.points[0].chapter_index == 0
    assert merged.timeline.points[0].previous == ""


def test_timeline_records_evolution_with_previous_value() -> None:
    merged = merge_persona_increments(
        [inc(personality="初现"), inc(personality="转变后")]
    )

    assert len(merged.timeline.points) == 2
    last = merged.timeline.points[-1]
    assert (last.chapter_index, last.previous, last.value) == (1, "初现", "转变后")


def test_timeline_ignores_unchanged_values() -> None:
    merged = merge_persona_increments([inc(personality="一样"), inc(personality="一样")])

    assert len(merged.timeline.points) == 1


def test_field_evolution_formats_early_to_late() -> None:
    """§7 的冲突格式：`"早期：X → 后期：Y"`。"""
    merged = merge_persona_increments(
        [inc(desire="想要自由"), inc(desire="想要留下")]
    )

    assert merged.field_evolution("desire") == "早期：想要自由 → 后期：想要留下"


def test_field_evolution_falls_back_to_current_value() -> None:
    merged = merge_persona_increments([inc(desire="想要自由")])

    assert merged.field_evolution("desire") == "想要自由"
    assert merged.field_evolution("flaw") == ""


def test_timeline_as_dicts_is_serialisable() -> None:
    import json

    merged = merge_persona_increments([inc(personality="初现"), inc(personality="转变")])

    payload = merged.timeline.as_dicts()
    json.dumps(payload, ensure_ascii=False)
    assert payload[0]["field"] == "personality"


def test_timeline_fields_lists_unique_names() -> None:
    merged = merge_persona_increments(
        [inc(personality="A1", desire="B1"), inc(personality="A2", desire="B2")]
    )

    assert sorted(merged.timeline.fields()) == ["desire", "personality"]


# ────────────────── 边界 ──────────────────


def test_merge_empty_list() -> None:
    merged = merge_persona_increments([])

    assert merged.persona.is_empty()
    assert merged.timeline.points == []


def test_merge_single_increment_returns_it() -> None:
    merged = merge_persona_increments([inc(personality="唯一")])

    assert merged.persona.personality == "唯一"


def test_chapter_index_tracks_position_in_list() -> None:
    """章节号来自传入顺序（空增量不占位，会跳过）。"""
    merged = merge_persona_increments(
        [inc(), inc(), inc(quotes=[Quote(field="flaw", text="第三章的句子。")])]
    )

    assert merged.persona.quotes[0].field == "flaw@ch2"
