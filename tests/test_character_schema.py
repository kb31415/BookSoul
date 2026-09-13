"""契约测试：序列化往返 / 默认值 / 立体字段（`PROJECT_DESIGN.md` §6.1、§6.4）。"""

from __future__ import annotations

from booksoul.schema import CharacterCard, LoreEntry, PlotNode, Relation, TimelineEvent
from conftest import make_full_card


def test_minimal_card_only_needs_name_and_description() -> None:
    """必填只有 name / description；其余字段必须都有默认值。"""
    card = CharacterCard(name="甲", description="乙")

    assert card.personality == ""
    assert card.scenario == ""
    assert card.first_mes == ""
    assert card.mes_example == ""
    assert card.character_book == []
    assert card.creator_notes == ""
    assert card.tags == []
    assert card.character_version == "1.0"
    # 立体扩展
    assert card.desire == ""
    assert card.flaw == ""
    assert card.secret == ""
    assert card.timeline == []
    assert card.relations == []
    assert card.plot_nodes == []
    # 元数据
    assert card.source_book is None
    assert card.extraction_meta == {}


def test_missing_required_fields_are_rejected() -> None:
    import pytest

    with pytest.raises(Exception):
        CharacterCard(name="只有名字")  # type: ignore[call-arg]


def test_model_dump_round_trip(full_card: CharacterCard) -> None:
    """model_dump() → model_validate() 应逐字段相等。"""
    dumped = full_card.model_dump()
    restored = CharacterCard.model_validate(dumped)

    assert restored == full_card
    # 结构化子模型类型没在往返中退化成 dict
    assert isinstance(restored.character_book[0], LoreEntry)
    assert isinstance(restored.timeline[0], TimelineEvent)
    assert isinstance(restored.relations[0], Relation)
    assert isinstance(restored.plot_nodes[0], PlotNode)


def test_model_dump_json_round_trip(full_card: CharacterCard) -> None:
    """走一遍真正的 JSON 文本，确保中文与嵌套结构无损。"""
    text = full_card.model_dump_json()
    restored = CharacterCard.model_validate_json(text)

    assert restored == full_card
    assert "沈知舟" in text


def test_json_round_trip_preserves_relation_direction(full_card: CharacterCard) -> None:
    """relations 单向存储：`from_` 字段名与 `from` JSON 键都要能用。"""
    first = full_card.relations[0]
    assert first.from_ == "沈知舟"
    assert first.to == "师妹"

    as_json = first.model_dump(by_alias=True)
    assert as_json["from"] == "沈知舟"
    assert "from_" not in as_json

    assert Relation(**as_json) == first  # 按 alias 构造
    assert Relation(from_="沈知舟", to="师妹", kind="师兄妹", description="表面冷淡，实则一路暗中照拂。", evolution=["初遇时只当她是累赘", "剑冢之后开始护着她"]) == first


def test_relation_from_field_is_optional_and_directional() -> None:
    """A→B 与 B→A 是两条不同记录，不做双向冗余（§6.3）。"""
    ab = Relation(**{"from": "沈知舟", "to": "师妹", "kind": "师兄妹"})
    ba = Relation(**{"from": "师妹", "to": "沈知舟", "kind": "师兄妹"})

    assert ab != ba
    assert Relation().from_ == ""


def test_unknown_keys_are_ignored_not_fatal() -> None:
    """外部卡塞了没见过的键，不能让导入崩掉。"""
    card = CharacterCard.model_validate(
        {
            "name": "甲",
            "description": "乙",
            "system_prompt": "v3 才有的字段",
            "post_history_instructions": "同上",
            "unknown_nested": {"a": 1},
        }
    )

    assert card.name == "甲"
    assert not hasattr(card, "system_prompt")


def test_lore_entry_defaults() -> None:
    entry = LoreEntry(keys=["剑冢"], content="埋着断剑。")

    assert entry.enabled is True
    assert entry.insertion_order == 0
    assert entry.case_sensitive is False


def test_timeline_event_optional_fields() -> None:
    event = TimelineEvent(id="t1", order=1, summary="雨夜剑冢", actors=["沈知舟"])

    assert event.chapter is None
    assert event.quote is None


def test_plot_node_defaults_to_canon_branch() -> None:
    node = PlotNode(id="p1", order=1, title="雨夜剑冢")

    assert node.branch == "canon"
    assert node.chapter is None


def test_has_extraction_evidence() -> None:
    """阶段 5 校验的抓手：有没有可溯源的原文引用。"""
    assert make_full_card().has_extraction_evidence is True
    assert CharacterCard(name="甲", description="乙").has_extraction_evidence is False
    # quotes 存在但是空 dict / 类型不对，都算"没有依据"
    assert (
        CharacterCard(name="甲", description="乙", extraction_meta={"quotes": {}})
        .has_extraction_evidence
        is False
    )
    assert (
        CharacterCard(name="甲", description="乙", extraction_meta={"quotes": "见第三章"})
        .has_extraction_evidence
        is False
    )


def test_required_fields_are_required_in_dump_shape() -> None:
    """`name` / `description` 是必填，dump 里必须出现。"""
    payload = CharacterCard(name="甲", description="乙").model_dump()

    assert set(payload) >= {"name", "description", "desire", "flaw", "secret"}
