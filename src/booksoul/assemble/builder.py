"""角色卡组装与校验（`MVP_PLAN.md` 阶段 5）。

目标：产出**可导入酒馆的完整角色卡**。

四个关键任务（照 `MVP_PLAN.md` 阶段 5）：

1. 把抽取结果组装成完整 `CharacterCard`（补默认值、装配 `character_book` 初版）
2. 校验：必填字段非空 + `extraction_meta` 中每条结论都有对应引用
3. 导出 v2 JSON → `data/cards/{character_id}.v2.json`
4. 人工检查：尝试导入 SillyTavern，或至少通过格式校验

字段映射（from `assemble/persona.PersonaIncrement` → `schema/character.CharacterCard`）：

| persona（Prompt 3 产物） | CharacterCard |
|---|---|
| `personality` / `desire` / `flaw` / `secret` / `speech_style` | 同名字段 |
| `relationships`（演化序列） | `relations`：同 target 的多次变化合成 `evolution` 列表 |
| `quotes` | `extraction_meta["quotes"]`（每条结论的原文依据） |
| `speech_samples` | `extraction_meta["speech_samples"]` |
| `changes` | `extraction_meta["changes"]` |
| 合并时间线（§7） | `timeline`：每个字段的演化点变成 `TimelineEvent` |
| Prompt 4 的 `first_mes` / `mes_example` | 同名字段 |

`description`（人设主体）由五个文本字段拼成 —— §6.1 说它是"身份 / 背景 / 外貌 /
性格综述"，而 MVP 只抽了性格类字段，所以这里如实拼、不编造没有的信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from booksoul.assemble.merge import MergedPersona, relationships_by_target
from booksoul.assemble.persona import PersonaIncrement
from booksoul.schema import CharacterCard, LoreEntry, Relation, TimelineEvent

__all__ = [
    "DEFAULT_TAG",
    "BuildReport",
    "build_description",
    "build_relations",
    "build_timeline",
    "build_card",
    "validate_card",
]

#: 自动加的第一个标签（来源标记：这本书抽出来的卡）。
DEFAULT_TAG = "书魂抽取"


@dataclass
class BuildReport:
    """组装 + 校验的汇总（CLI 打印 & 人工把关用）。"""

    character: str
    book_id: str = ""
    field_lengths: dict[str, int] = field(default_factory=dict)
    relation_count: int = 0
    relation_evolution_total: int = 0
    timeline_count: int = 0
    quote_count: int = 0
    speech_sample_count: int = 0
    missing_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "character": self.character,
            "book_id": self.book_id,
            "field_lengths": dict(self.field_lengths),
            "relation_count": self.relation_count,
            "relation_evolution_total": self.relation_evolution_total,
            "timeline_count": self.timeline_count,
            "quote_count": self.quote_count,
            "speech_sample_count": self.speech_sample_count,
            "missing_required": list(self.missing_required),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


# ══════════════════════════ 字段组装 ══════════════════════════


def build_description(persona: PersonaIncrement, *, source_book: str = "") -> str:
    """把 persona 的文本字段拼成 `description`（§6.1：人设主体）。

    **不编造没抽到的信息**：MVP 只抽了性格类字段，所以这里就是这几项的如实汇总
    （身份/外貌要等 Prompt 3 扩字段，属"完善"阶段）。
    """
    labels = (
        ("personality", "性格"),
        ("desire", "欲望"),
        ("flaw", "缺陷"),
        ("secret", "秘密"),
        ("speech_style", "说话风格"),
    )
    lines = [f"{label}：{getattr(persona, name)}" for name, label in labels if getattr(persona, name)]
    if source_book:
        lines.append(f"来源：《{source_book}》")
    return "\n".join(lines)


def build_relations(persona: PersonaIncrement, *, character: str) -> list[Relation]:
    """把 `relationships` 演化序列转成 `Relation` 列表（单向存储，§6.3）。

    同一 `target` 的多次变化合成一条 `Relation`，变化全部进 `evolution`
    （§7：同一 target 的多次变化全部保留，形成演化序列）。
    """
    grouped = relationships_by_target(persona)
    relations: list[Relation] = []
    for target, changes in grouped.items():
        relations.append(
            Relation(
                **{
                    "from": character,
                    "to": target,
                    "kind": "",
                    # description 取最新一次变化（最能代表当前状态）
                    "description": changes[-1] if changes else "",
                    "evolution": list(changes),
                }
            )
        )
    return relations


def build_timeline(merged: MergedPersona, *, limit: int = 200) -> list[TimelineEvent]:
    """把合并过程记下的时间线转成 `TimelineEvent` 列表（设计 §6.1 的先天记忆载体）。

    `limit` 兜住长度：85 章抽完可能有几百个点，而 §5.4 的上下文预算有限。
    按章号升序取前 `limit` 个（人设**立起来**的阶段比后面更重要）。
    """
    events: list[TimelineEvent] = []
    for order, point in enumerate(merged.timeline.points[:limit]):
        summary = f"{point.field}：{point.value}"
        if point.previous:
            summary = f"{point.field}：{point.previous} → {point.value}"
        events.append(
            TimelineEvent(
                id=f"t{order}",
                order=order,
                summary=summary,
                actors=[],
                chapter=f"第{point.chapter_index}章",
                quote=point.previous or None,
            )
        )
    return events


def build_card(
    character: str,
    merged: MergedPersona,
    *,
    book_id: str = "",
    source_book: str = "",
    first_mes: str = "",
    mes_example: str = "",
    tags: list[str] | None = None,
    character_book: list[LoreEntry] | None = None,
    timeline_limit: int = 200,
) -> tuple[CharacterCard, BuildReport]:
    """把 persona + Prompt 4 的产物组装成完整 `CharacterCard`。

    返回 `(卡片, 组装报告)`；校验结果写进报告（`validate_card` 单独可调）。
    """
    persona = merged.persona
    relations = build_relations(persona, character=character)
    timeline = build_timeline(merged, limit=timeline_limit)

    card = CharacterCard(
        name=character,
        description=build_description(persona, source_book=source_book),
        personality=persona.personality,
        # scenario 暂无来源（§6.1 是"开场情境"）—— 不编造，留空
        first_mes=first_mes,
        mes_example=mes_example,
        character_book=list(character_book or []),
        tags=list(dict.fromkeys([DEFAULT_TAG, *(tags or [])])),
        desire=persona.desire,
        flaw=persona.flaw,
        secret=persona.secret,
        speech_style=persona.speech_style,
        timeline=timeline,
        relations=relations,
        source_book=source_book or None,
        extraction_meta={
            "quotes": [quote.model_dump(mode="json") for quote in persona.quotes],
            "speech_samples": list(persona.speech_samples),
            "changes": [change.model_dump(mode="json", by_alias=True) for change in persona.changes],
            "book_id": book_id,
            "generator": "booksoul/阶段4-5",
        },
    )

    report = BuildReport(
        character=character,
        book_id=book_id,
        field_lengths={
            name: len(getattr(card, name))
            for name in (
                "name",
                "description",
                "personality",
                "desire",
                "flaw",
                "secret",
                "speech_style",
                "first_mes",
                "mes_example",
            )
        },
        relation_count=len(relations),
        relation_evolution_total=sum(len(relation.evolution) for relation in relations),
        timeline_count=len(timeline),
        quote_count=len(persona.quotes),
        speech_sample_count=len(persona.speech_samples),
    )
    validate_card(card, report)
    return card, report


# ══════════════════════════ 校验（阶段 5 任务 2）══════════════════════════

#: 必填字段（§6.1 标 ✅ 的只有 name / description；这里按"可用的卡"再加两项）。
REQUIRED_FIELDS: tuple[str, ...] = ("name", "description", "personality")

#: 期望非空的扩展字段（缺失只告警，不阻塞 —— 有些角色确实没有秘密）。
EXPECTED_FIELDS: tuple[str, ...] = ("desire", "flaw", "secret", "speech_style")


def validate_card(card: CharacterCard, report: BuildReport | None = None) -> BuildReport:
    """校验卡片（阶段 5 任务 2）：**必填非空 + 每条结论都有引用**。

    这里的"有引用"判据比阶段 4 的 `validate_persona_increment` 宽：阶段 4 已把
    无依据的字段置空了，这里只做**最终确认**（卡片上非空的文本字段，在
    `extraction_meta["quotes"]` 里应该有对应的 `field`）。
    """
    target = report or BuildReport(character=card.name)

    for name in REQUIRED_FIELDS:
        if not getattr(card, name, "").strip():
            target.missing_required.append(name)
    for name in EXPECTED_FIELDS:
        if not getattr(card, name, "").strip():
            target.warnings.append(f"{name} 为空（该角色可能确实没有这一项）")

    # 引用覆盖：非空文本字段是否都有 quotes 条目（field 可能带 @chN 章节标记）
    quoted_fields = {
        str(quote.get("field", "")).split("@ch", 1)[0]
        for quote in card.extraction_meta.get("quotes", [])
        if isinstance(quote, dict)
    }
    for name in ("personality", "desire", "flaw", "secret", "speech_style"):
        if getattr(card, name).strip() and name not in quoted_fields:
            target.warnings.append(f"{name} 没有对应的原文引用（阶段 4 应已置空，请复核）")

    # §6.1 / 常见坑：mes_example 的 <START> 轮次格式
    if card.mes_example:
        if "<START>" not in card.mes_example:
            target.warnings.append("mes_example 缺少 <START> 轮次分隔（酒馆格式要求）")
        if "{{user}}" not in card.mes_example and "{{char}}" not in card.mes_example:
            target.warnings.append("mes_example 没有 {{user}} / {{char}} 占位符")

    # §6.1：first_mes 可选，但一旦有就该有钩子的基本形态（只提醒长度）
    if card.first_mes and not (20 <= len(card.first_mes) <= 300):
        target.warnings.append(f"first_mes 长度 {len(card.first_mes)}（§6 建议 50–150 字）")

    target.ok = not target.missing_required
    return target


def card_to_v2_payload(card: CharacterCard) -> dict[str, Any]:
    """导出用的 v2 JSON（就是 `to_tavern_v2()`，这里只是给个语义更清楚的名字）。"""
    return card.to_tavern_v2()


class CardBundle(BaseModel):
    """一次组装的完整交付物（落盘用，便于阶段 6 直接读）。"""

    model_config = ConfigDict(extra="ignore")

    card: dict[str, Any] = Field(default_factory=dict)
    v2: dict[str, Any] = Field(default_factory=dict)
    build_report: dict[str, Any] = Field(default_factory=dict)
