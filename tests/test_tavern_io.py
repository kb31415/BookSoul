"""酒馆 v2/v3 导入导出测试（`PROJECT_DESIGN.md` §6.2）。

覆盖三类导入形态：

1. 有 `spec` + `data` 包装（chub.ai 主流）
2. 无包装的裸字段（老卡）
3. 有 `spec` 但字段平铺、缺 `extensions` 的卡

以及 `character_book` 的两种写法（v1 列表 / v2 `{"entries": []}`）。
"""

from __future__ import annotations

from typing import Any

from booksoul.schema import (
    BOOKSOUL_EXTENSION_KEY,
    SPEC_V2,
    SPEC_V2_VERSION,
    SPEC_V3,
    CharacterCard,
    LoreEntry,
)
from booksoul.schema.tavern import from_tavern, to_tavern_v2
from conftest import make_full_card

STANDARD_FIELDS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creator_notes",
    "tags",
    "character_version",
    "character_book",
    "extensions",
)


# ────────────────────────────── 导出结构 ──────────────────────────────


def test_export_has_v2_envelope(full_card: CharacterCard) -> None:
    payload = to_tavern_v2(full_card)

    assert payload["spec"] == SPEC_V2
    assert payload["spec_version"] == SPEC_V2_VERSION
    assert set(payload) == {"spec", "spec_version", "data"}


def test_export_puts_standard_fields_directly_under_data(full_card: CharacterCard) -> None:
    data = to_tavern_v2(full_card)["data"]

    assert set(data) >= set(STANDARD_FIELDS)
    assert data["name"] == "沈知舟"
    assert data["description"] == full_card.description
    assert data["personality"] == full_card.personality
    assert data["scenario"] == full_card.scenario
    assert data["first_mes"] == full_card.first_mes
    assert data["mes_example"].startswith("<START>")
    assert data["creator_notes"] == full_card.creator_notes
    assert data["tags"] == ["古风", "师兄", "仙侠"]
    assert data["character_version"] == "1.2"

    # 标准字段不重复出现在 extensions 里
    booksoul = data["extensions"][BOOKSOUL_EXTENSION_KEY]
    assert "name" not in booksoul
    assert "description" not in booksoul
    assert "first_mes" not in booksoul


def test_export_collects_structured_fields_into_extensions_booksoul(
    full_card: CharacterCard,
) -> None:
    booksoul = to_tavern_v2(full_card)["data"]["extensions"][BOOKSOUL_EXTENSION_KEY]

    assert set(booksoul) >= {
        "desire",
        "flaw",
        "secret",
        "timeline",
        "relations",
        "plot_nodes",
    }
    assert booksoul["desire"] == full_card.desire
    assert booksoul["flaw"] == full_card.flaw
    assert booksoul["secret"] == full_card.secret
    assert booksoul["timeline"][0]["id"] == "t1"
    assert booksoul["plot_nodes"][0]["branch"] == "canon"
    assert booksoul["source_book"] == "青云旧事"
    assert booksoul["extraction_meta"]["quotes"]


def test_export_serialises_relation_from_key_as_from(full_card: CharacterCard) -> None:
    """JSON 里的键必须是 `from`，不是 `from_`。"""
    relations = to_tavern_v2(full_card)["data"]["extensions"][BOOKSOUL_EXTENSION_KEY][
        "relations"
    ]

    assert relations[0]["from"] == "沈知舟"
    assert relations[0]["to"] == "师妹"
    assert "from_" not in relations[0]


def test_export_is_json_serialisable_with_chinese_intact(full_card: CharacterCard) -> None:
    import json

    text = json.dumps(to_tavern_v2(full_card), ensure_ascii=False)
    reloaded = json.loads(text)

    assert reloaded["data"]["name"] == "沈知舟"
    assert "月白长衫" in text


def test_export_character_book_entries_carry_keys_and_content(
    full_card: CharacterCard,
) -> None:
    book = to_tavern_v2(full_card)["data"]["character_book"]

    assert isinstance(book, dict) and "entries" in book
    entries = book["entries"]
    assert entries[0]["keys"] == ["剑冢", "后山"]
    assert entries[0]["insertion_order"] == 10
    assert entries[1]["enabled"] is False


def test_method_and_function_agree(full_card: CharacterCard) -> None:
    """`card.to_tavern_v2()` 与 `to_tavern_v2(card)` 必须一致。"""
    assert full_card.to_tavern_v2() == to_tavern_v2(full_card)


# ────────────────────────────── 往返 ──────────────────────────────


def test_export_import_round_trip_is_lossless(full_card: CharacterCard) -> None:
    restored = from_tavern(to_tavern_v2(full_card))

    assert restored.name == full_card.name
    assert restored.description == full_card.description
    assert restored.personality == full_card.personality
    assert restored.scenario == full_card.scenario
    assert restored.first_mes == full_card.first_mes
    assert restored.mes_example == full_card.mes_example
    assert restored.tags == full_card.tags
    assert restored.character_version == full_card.character_version
    assert restored.desire == full_card.desire
    assert restored.flaw == full_card.flaw
    assert restored.secret == full_card.secret
    assert restored.source_book == full_card.source_book
    assert restored.extraction_meta == full_card.extraction_meta
    assert restored.relations == full_card.relations
    assert restored.timeline == full_card.timeline
    assert restored.plot_nodes == full_card.plot_nodes
    assert restored.character_book == full_card.character_book


def test_round_trip_is_stable_across_two_cycles(full_card: CharacterCard) -> None:
    once = to_tavern_v2(full_card)
    twice = to_tavern_v2(from_tavern(once))

    assert twice == once


def test_classmethod_import_matches_function(full_card: CharacterCard) -> None:
    payload = to_tavern_v2(full_card)

    assert CharacterCard.from_tavern(payload) == from_tavern(payload)


# ────────────────────────────── 导入兼容性 ──────────────────────────────


def test_import_spec_wrapped_card_with_extensions(raw_wrapped_card: dict[str, Any]) -> None:
    card = from_tavern(raw_wrapped_card)

    assert card.name == "沈知舟"
    assert card.tags == ["古风"]
    assert card.desire == "让师妹活着离开。"
    assert card.flaw == "不解释。"
    assert card.secret == "他放走了叛徒。"
    assert card.source_book == "青云旧事"
    assert card.relations[0].from_ == "沈知舟"
    assert card.timeline[0].summary == "雨夜剑冢"
    # 外部卡的 quotes 原样保留；同时把我们没建模的 v2 条目形状留在 lore 里
    assert card.extraction_meta["quotes"] == {"flaw": "「不必问。」"}
    assert card.extraction_meta["lore"] == [
        {"id": 7, "position": "before_char", "extensions": {"book": "upstream"}}
    ]


def test_import_keeps_unknown_v2_lore_keys_on_round_trip(
    raw_wrapped_card: dict[str, Any],
) -> None:
    """v2 条目的 id / position / extensions 不该被我们吃掉。"""
    card = from_tavern(raw_wrapped_card)

    assert card.character_book[0].keys == ["剑冢"]
    assert card.character_book[0].content == "埋着断剑。"
    assert card.character_book[0].insertion_order == 10

    re_exported = to_tavern_v2(card)["data"]["character_book"]["entries"][0]
    assert re_exported["id"] == 7
    assert re_exported["position"] == "before_char"
    assert re_exported["extensions"] == {"book": "upstream"}


def test_import_plain_legacy_card(raw_plain_card: dict[str, Any]) -> None:
    """裸字段老卡：缺 extensions 不报错，立体字段取默认值。"""
    card = from_tavern(raw_plain_card)

    assert card.name == "无名剑客"
    assert card.description == "一个只留下背影的人。"
    assert card.character_book[0].content == "他从不回头。"
    assert card.desire == ""
    assert card.flaw == ""
    assert card.secret == ""
    assert card.timeline == []
    assert card.relations == []
    assert card.plot_nodes == []


def test_import_tolerates_dirty_types(raw_plain_card: dict[str, Any]) -> None:
    """tags 是 str、character_version 是 None —— 收敛而不是崩。"""
    card = from_tavern(raw_plain_card)

    assert card.tags == ["独行"]
    assert card.character_version == "1.0"


def test_import_spec_without_data_wrapper() -> None:
    """有 spec 但字段平铺（没有 data 包装）。"""
    card = from_tavern(
        {
            "spec": SPEC_V2,
            "name": "平铺角色",
            "description": "字段直接在顶层。",
            "first_mes": "「来了。」",
        }
    )

    assert card.name == "平铺角色"
    assert card.description == "字段直接在顶层。"
    assert card.first_mes == "「来了。」"


def test_import_v3_envelope() -> None:
    card = from_tavern(
        {
            "spec": SPEC_V3,
            "spec_version": "3.0",
            "data": {
                "name": "v3 角色",
                "description": "带 v3 专有字段。",
                "system_prompt": "这段应被忽略。",
                "character_book": {"entries": []},
            },
        }
    )

    assert card.name == "v3 角色"
    assert card.character_book == []


def test_import_missing_optional_fields_uses_defaults() -> None:
    """只给必填两件套，其余全缺。"""
    card = from_tavern({"name": "甲", "description": "乙"})

    assert card == CharacterCard(name="甲", description="乙")


def test_import_empty_extensions_does_not_break() -> None:
    card = from_tavern(
        {
            "spec": SPEC_V2,
            "data": {"name": "甲", "description": "乙", "extensions": {}},
        }
    )

    assert card.desire == "" and card.relations == []


def test_import_extensions_without_booksoul_namespace() -> None:
    """别的工具写的 extensions（pytorch 之类）不该被误当成我们的。"""
    card = from_tavern(
        {
            "spec": SPEC_V2,
            "data": {
                "name": "甲",
                "description": "乙",
                "extensions": {"depth_prompt": {"depth": 4}},
            },
        }
    )

    assert card.desire == ""
    assert card.extraction_meta == {}


def test_import_character_book_as_plain_list() -> None:
    """v1 风格：character_book 直接是列表。"""
    card = from_tavern(
        {
            "name": "甲",
            "description": "乙",
            "character_book": [
                {"keys": ["钥匙"], "content": "内容"},
                {"key": "单选键", "content": "另一种写法"},
            ],
        }
    )

    assert len(card.character_book) == 2
    assert card.character_book[0].keys == ["钥匙"]
    assert card.character_book[1].keys == ["单选键"]


def test_import_character_book_with_odd_shapes() -> None:
    """character_book 是 null / 字符串 / 条目是垃圾 —— 一律退化成空或跳过。"""
    for book in (None, "不是列表", 7, {"entries": None}, [None, "x", 3]):
        card = from_tavern({"name": "甲", "description": "乙", "character_book": book})
        assert card.character_book == []


def test_import_falls_back_to_legacy_field_names() -> None:
    card = from_tavern(
        {
            "name": "甲",
            "description": "乙",
            "first_message": "旧字段名",
            "example_dialogue": "<START>\n{{char}}: 旧字段名",
        }
    )

    assert card.first_mes == "旧字段名"
    assert card.mes_example.endswith("旧字段名")


def test_import_structured_fields_flat_in_data() -> None:
    """有些自家中间产物把立体字段平铺在 data 里，也认。"""
    card = from_tavern(
        {
            "spec": SPEC_V2,
            "data": {
                "name": "甲",
                "description": "乙",
                "desire": "平铺的欲望",
                "relations": [{"from": "甲", "to": "乙", "kind": "故人"}],
            },
        }
    )

    assert card.desire == "平铺的欲望"
    assert card.relations[0].to == "乙"


def test_import_prefers_booksoul_namespace_over_flat_field() -> None:
    card = from_tavern(
        {
            "spec": SPEC_V2,
            "data": {
                "name": "甲",
                "description": "乙",
                "desire": "平铺的（应被忽略）",
                "extensions": {BOOKSOUL_EXTENSION_KEY: {"desire": "扩展里的（应采用）"}},
            },
        }
    )

    assert card.desire == "扩展里的（应采用）"


def test_import_non_dict_raises_type_error() -> None:
    import pytest

    with pytest.raises(TypeError):
        from_tavern("不是 dict")  # type: ignore[arg-type]


def test_exported_card_can_be_reimported_after_manual_edit(
    raw_wrapped_card: dict[str, Any],
) -> None:
    """人工改过角色卡（阶段 5 会做的事）后仍然能导入。"""
    card = from_tavern(raw_wrapped_card)
    card.first_mes = "人工改写过的开场白。"
    card.tags.append("人工加标签")

    again = from_tavern(to_tavern_v2(card))

    assert again.first_mes == "人工改写过的开场白。"
    assert "人工加标签" in again.tags
    assert again.character_book[0].content == "埋着断剑。"


def test_export_has_no_extraction_meta_lore_for_clean_books() -> None:
    """纯 keys/content 的条目不用留底，避免 extensions 无意义膨胀。"""
    card = CharacterCard(
        name="甲",
        description="乙",
        # 直接构造的 LoreEntry 没有原始 v2 形状
        character_book=[LoreEntry(keys=["k"], content="c")],
    )

    booksoul = to_tavern_v2(card)["data"]["extensions"][BOOKSOUL_EXTENSION_KEY]
    assert "lore" not in booksoul


def test_full_card_round_trip_via_json_text(full_card: CharacterCard) -> None:
    import json

    text = json.dumps(to_tavern_v2(full_card), ensure_ascii=False, indent=2)
    restored = from_tavern(json.loads(text))

    assert restored.to_tavern_v2() == to_tavern_v2(full_card)
    assert restored.name == make_full_card().name
