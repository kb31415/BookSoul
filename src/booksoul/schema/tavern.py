"""酒馆（Tavern）v2/v3 角色卡导入导出（`PROJECT_DESIGN.md` §6.2）。

三条关键决策：

1. **标准字段直出 `data.*`** —— 满足 chub.ai / 酒馆规范，外部工具可直接用。
2. **立体扩展统一收进 `data.extensions.booksoul`** —— 外部工具忽略未知 extension
   不受影响；自家内核只读这一处。
3. **导入兼容三种形态**（自动识别，缺字段一律取默认值、不报错）：

   - `{"spec": "chara_card_v2"|"chara_card_v3", "data": {...}}`：chub.ai 主流包装
   - `{"spec": ..., ...裸字段...}`：有 spec 但字段平铺在顶层
   - `{...裸字段...}`：老卡的裸字段格式（无 `spec`、无 `extensions`）

另外兼容 `character_book` 的两种写法：v1 的**列表**与 v2 的 `{"entries": [...]}`。
导入 v2 条目时，`entries` 的未知键会原样留在
`extensions.booksoul.lore[i]` 里，导出时再合回去，保证往返不丢信息。
"""

from __future__ import annotations

from typing import Any

from booksoul.schema.character import (
    CharacterCard,
    LoreEntry,
    PlotNode,
    Relation,
    TimelineEvent,
)

__all__ = [
    "BOOKSOUL_EXTENSION_KEY",
    "SPEC_V2",
    "SPEC_V2_VERSION",
    "SPEC_V3",
    "SPEC_V3_VERSION",
    "from_tavern",
    "to_tavern_v2",
]

SPEC_V2 = "chara_card_v2"
SPEC_V2_VERSION = "2.0"
SPEC_V3 = "chara_card_v3"
SPEC_V3_VERSION = "3.0"

#: 立体扩展在 `data.extensions` 下的命名空间。
BOOKSOUL_EXTENSION_KEY = "booksoul"

_KNOWN_SPECS = {SPEC_V2, SPEC_V3}

#: 不认识就忽略的元字段（避免污染 CharacterCard）。
_META_KEYS = frozenset({"spec", "spec_version", "data", "extensions", "avatar", "create_date"})

_STRUCTURED_KEYS = (
    "desire",
    "flaw",
    "secret",
    "speech_style",
    "timeline",
    "relations",
    "plot_nodes",
)

#: 这些键由 `LoreEntry` 自己承载，导出时总会重写 —— 所以导入时不算"原始信息"，
#: 否则会把我们刚写出去的字段当成外部信息留底，导致往返回流不收敛。
_LORE_SYNTHESIZED_KEYS = frozenset(
    {"keys", "key", "content", "enabled", "insertion_order", "case_sensitive"}
)


# ────────────────────────────── 导出 ──────────────────────────────


def _card_to_lore_entry(entry: LoreEntry, origin: dict[str, Any] | None = None) -> dict[str, Any]:
    """LoreEntry → v2 条目。`origin` 是导入时留下的原始 v2 条目。"""
    base: dict[str, Any] = dict(origin) if origin else {}
    base.update(
        {
            "keys": list(entry.keys),
            "content": entry.content,
            "enabled": entry.enabled,
            "insertion_order": entry.insertion_order,
            "case_sensitive": entry.case_sensitive,
        }
    )
    return base


def _booksoul_extension(card: CharacterCard) -> dict[str, Any]:
    """立体扩展 + 元数据 → `data.extensions.booksoul`。"""
    lore_origins = card.extraction_meta.get("lore")
    lore_origins = lore_origins if isinstance(lore_origins, list) else []

    extension: dict[str, Any] = {
        "desire": card.desire,
        "flaw": card.flaw,
        "secret": card.secret,
        "speech_style": card.speech_style,
        "timeline": [e.model_dump(mode="json") for e in card.timeline],
        "relations": [r.model_dump(mode="json", by_alias=True) for r in card.relations],
        "plot_nodes": [n.model_dump(mode="json") for n in card.plot_nodes],
    }

    if card.source_book is not None:
        extension["source_book"] = card.source_book
    if card.extraction_meta:
        extension["extraction_meta"] = card.extraction_meta

    # 保留导入时的 v2 条目原始形状，避免往返丢字段。
    reserved: list[dict[str, Any]] = []
    for index, entry in enumerate(card.character_book):
        origin = lore_origins[index] if index < len(lore_origins) else None
        if isinstance(origin, dict):
            reserved.append(origin)
    if reserved:
        extension["lore"] = reserved

    return extension


def to_tavern_v2(card: CharacterCard) -> dict[str, Any]:
    """把 `CharacterCard` 导出为酒馆 v2 角色卡。"""
    raw_origins = card.extraction_meta.get("lore")
    origins = raw_origins if isinstance(raw_origins, list) else []

    entries: list[dict[str, Any]] = []
    for index, entry in enumerate(card.character_book):
        origin = origins[index] if index < len(origins) else None
        entries.append(_card_to_lore_entry(entry, origin if isinstance(origin, dict) else None))

    data: dict[str, Any] = {
        "name": card.name,
        "description": card.description,
        "personality": card.personality,
        "scenario": card.scenario,
        "first_mes": card.first_mes,
        "mes_example": card.mes_example,
        "creator_notes": card.creator_notes,
        "tags": list(card.tags),
        "character_version": card.character_version,
        # 默认导出为对象形式，同时便于 v1 消费者读取 entries。
        "character_book": {"entries": entries},
        "extensions": {BOOKSOUL_EXTENSION_KEY: _booksoul_extension(card)},
    }

    return {"spec": SPEC_V2, "spec_version": SPEC_V2_VERSION, "data": data}


# ────────────────────────────── 导入 ──────────────────────────────


def _unwrap(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """剥掉 spec 包装，返回 `(data, extensions)`。

    兼容：① 有 `spec` + `data` 包装；② 有 `spec` 但字段平铺；
    ③ 完全没有包装的裸字段。
    """
    if not isinstance(raw, dict):
        raise TypeError(f"角色卡必须是 dict，收到 {type(raw).__name__}")

    spec = raw.get("spec")
    inner = raw.get("data") if isinstance(raw.get("data"), dict) else None

    if spec in _KNOWN_SPECS and inner is not None:
        data = dict(inner)
        extensions = data.get("extensions")
        return data, extensions if isinstance(extensions, dict) else {}

    # 未知 spec 但有 data：在数据本身没有 name 时也当作包装处理。
    if inner is not None and "name" not in raw:
        data = dict(inner)
        extensions = data.get("extensions")
        return data, extensions if isinstance(extensions, dict) else {}

    data = {k: v for k, v in raw.items() if k not in ("data", "extensions")}
    extensions = raw.get("extensions")
    return data, extensions if isinstance(extensions, dict) else {}


def _as_str(value: Any) -> str:
    """宽容地把任意值收敛成 str（外部卡里常见 null / 数字）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [_as_str(item) for item in value]
    return [_as_str(value)]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _parse_character_book(raw_book: Any) -> tuple[list[LoreEntry], list[dict[str, Any]]]:
    """解析 `character_book`：兼容 `{"entries": [...]}` 与裸列表。"""
    if isinstance(raw_book, dict):
        raw_entries = raw_book.get("entries")
    else:
        raw_entries = raw_book
    if not isinstance(raw_entries, list):
        return [], []

    entries: list[LoreEntry] = []
    origins: list[dict[str, Any]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        entries.append(
            LoreEntry(
                keys=_as_str_list(item.get("keys") or item.get("key")),
                content=_as_str(item.get("content")),
                enabled=_as_bool(item.get("enabled"), default=True),
                insertion_order=_as_int(item.get("insertion_order")),
                case_sensitive=_as_bool(item.get("case_sensitive")),
            )
        )
        origins.append(dict(item))
    return entries, origins


def _parse_models(model: type, raw_items: Any) -> list[Any]:
    if not isinstance(raw_items, list):
        return []
    parsed: list[Any] = []
    for item in raw_items:
        if isinstance(item, dict):
            parsed.append(model.model_validate(item))
        elif isinstance(item, model):
            parsed.append(item)
    return parsed


def _extract_structured(
    data: dict[str, Any], extension: dict[str, Any]
) -> dict[str, Any]:
    """取立体字段：优先 `extensions.booksoul`，缺失时回退到顶层裸字段。"""
    resolved: dict[str, Any] = {}
    for key in _STRUCTURED_KEYS:
        if key in extension:
            resolved[key] = extension[key]
        elif key in data:
            # 有些自家中间产物会把立体字段平铺在 data 里，也认。
            resolved[key] = data[key]
        else:
            resolved[key] = None
    return resolved


def from_tavern(raw: dict[str, Any]) -> CharacterCard:
    """从酒馆 v2/v3 卡（或裸字段卡）导入 `CharacterCard`。

    任何缺失字段都取默认值 —— 导入外部卡绝不因为缺 `extensions` 而崩。
    """
    data, extensions = _unwrap(raw)
    extension = extensions.get(BOOKSOUL_EXTENSION_KEY)
    extension = extension if isinstance(extension, dict) else {}

    structured = _extract_structured(data, extension)
    book, lore_origins = _parse_character_book(data.get("character_book"))

    extraction_meta = extension.get("extraction_meta")
    extraction_meta = dict(extraction_meta) if isinstance(extraction_meta, dict) else {}
    if lore_origins:
        # 只保留"我们没建模、也不重写"的键（如 v2 的 id / position / name）；
        # 纯 keys/content 的条目不用留底，避免 extensions 无意义膨胀。
        trimmed = [
            {k: v for k, v in origin.items() if k not in _LORE_SYNTHESIZED_KEYS}
            for origin in lore_origins
        ]
        if any(trimmed):
            extraction_meta["lore"] = trimmed

    source_book = extension.get("source_book", data.get("source_book"))

    return CharacterCard(
        name=_as_str(data.get("name")),
        description=_as_str(data.get("description")),
        personality=_as_str(data.get("personality")),
        scenario=_as_str(data.get("scenario")),
        first_mes=_as_str(data.get("first_mes") or data.get("first_message")),
        mes_example=_as_str(data.get("mes_example") or data.get("example_dialogue")),
        character_book=book,
        creator_notes=_as_str(data.get("creator_notes")),
        tags=_as_str_list(data.get("tags")),
        character_version=_as_str(data.get("character_version")) or "1.0",
        desire=_as_str(structured.get("desire")),
        flaw=_as_str(structured.get("flaw")),
        secret=_as_str(structured.get("secret")),
        speech_style=_as_str(structured.get("speech_style")),
        timeline=_parse_models(TimelineEvent, structured.get("timeline")),
        relations=_parse_models(Relation, structured.get("relations")),
        plot_nodes=_parse_models(PlotNode, structured.get("plot_nodes")),
        source_book=_as_str(source_book) or None,
        extraction_meta=extraction_meta,
    )
