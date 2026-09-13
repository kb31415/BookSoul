"""多章 persona 增量的合并规则（`PROMPT_DESIGN.md` §7）。

§7 那张表逐条实现 —— 规则必须明确，否则"实现会各写各的"：

| 字段 | 合并规则 |
|---|---|
| `personality` / `desire` / `flaw` / `secret` / `speech_style` | **非空覆盖空**；都有值时保留**较晚出现**的；若为冲突则记入 `changes` 并保留两者（格式 `"早期：X → 后期：Y"`） |
| `relationships` | 按 `target` 聚合；同一 target 的多次变化**全部保留**，形成演化序列 |
| `quotes` | 全部保留（去重），附上章节号 |
| `speech_samples` | 全部保留，最终由 Prompt 4 挑选 |
| `changes` | 全部保留，是"成长弧光"的原始素材 |

**时间线副作用**：合并过程记录"哪个字段在第几章发生变化"，这就是设计 §6 里
`timeline` 的雏形，为第二阶段的"成长弧光"打底（§7 结尾）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from booksoul.assemble.persona import (
    PERSONA_TEXT_FIELDS,
    PersonaChange,
    PersonaIncrement,
    Quote,
)

__all__ = [
    "MergedPersona",
    "PersonaTimeline",
    "TimelinePoint",
    "merge_persona_increments",
    "relationships_by_target",
]


@dataclass
class TimelinePoint:
    """某个字段在第几章变成了什么（「成长弧光」的原始素材）。"""

    chapter_index: int
    field: str
    value: str
    previous: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "chapter_index": self.chapter_index,
            "field": self.field,
            "value": self.value,
            "previous": self.previous,
            "reason": self.reason,
        }


@dataclass
class PersonaTimeline:
    """合并过程中累积的时间线（§7 的"副作用"）。"""

    points: list[TimelinePoint] = field(default_factory=list)

    def record(self, chapter_index: int, field_name: str, value: str, previous: str) -> None:
        self.points.append(
            TimelinePoint(
                chapter_index=chapter_index,
                field=field_name,
                value=value,
                previous=previous,
            )
        )

    def fields(self) -> list[str]:
        seen: dict[str, None] = {}
        for point in self.points:
            seen.setdefault(point.field, None)
        return list(seen)

    def as_dicts(self) -> list[dict[str, object]]:
        return [point.as_dict() for point in self.points]


def _evolution_quote(previous: str, current: str) -> str:
    """§7 的冲突格式：`"早期：X → 后期：Y"`。"""
    return f"早期：{previous} → 后期：{current}"


def _dedupe_quotes(quotes: Iterable[Quote], chapter_index: int) -> list[Quote]:
    """§7：`quotes` 全部保留（去重），**附上章节号**。

    去重键是 `(原始字段名, 原文)` —— 刻意与 `field` 上的章节标记无关，
    这样"同一句原文被两章各抽到一次"只留一条，且保留**首次出现**的章节
    （出处以最早那次为准；`personality@ch0` 本身就是"引文出自第 0 章"的标注）。
    """
    seen: set[tuple[str, str]] = set()
    result: list[Quote] = []
    for quote in quotes:
        base_field = quote.field.split("@ch", 1)[0]
        key = (base_field, quote.text)
        if key in seen:
            continue
        seen.add(key)
        tagged = quote.model_copy(
            update={
                # 章节号附在 field 后（沿用 §5 输出的字段结构，不额外造字段）
                "field": base_field if not chapter_index else f"{base_field}@ch{chapter_index}",
            }
        )
        result.append(tagged)
    return result


@dataclass
class MergedPersona:
    """合并结果 = 最终 persona + 合并过程中记下的时间线。

    刻意**不**把时间线挂到 `PersonaIncrement` 上（那要往 pydantic 模型里塞私有属性，
    又脏又脆）—— 两者分开返回，谁需要谁拿。
    """

    persona: PersonaIncrement
    timeline: PersonaTimeline

    def field_evolution(self, field_name: str) -> str:
        """某个字段的演化文本（§7 的 `"早期：X → 后期：Y"`）。"""
        sequence = [point for point in self.timeline.points if point.field == field_name]
        if len(sequence) <= 1:
            return getattr(self.persona, field_name, "")
        first, last = sequence[0], sequence[-1]
        if first.value == last.value:
            return last.value
        return _evolution_quote(first.value, last.value)


def merge_persona_increments(increments: list[PersonaIncrement]) -> MergedPersona:
    """按 §7 把逐章增量合并成最终 persona。

    `increments` 按章节顺序传入（**索引即章号来源**，空增量也要占位）。
    """
    merged = PersonaIncrement()
    timeline = PersonaTimeline()

    for chapter_index, increment in enumerate(increments):
        if increment is None:
            continue

        # 文本字段：只有"文本字段全空"时才跳过这一步。
        # **不能**因为 `is_empty()` 就整体跳过 —— 那会连 quotes / speech_samples
        # 一起丢掉，而 §7 要求这两者"全部保留"（LLM 完全可能抽到引用却给不出成句的
        # personality，这种增量仍有信息量）。
        if not increment.is_empty():
            for name in PERSONA_TEXT_FIELDS:
                incoming = getattr(increment, name).strip()
                if not incoming:
                    continue

                current = getattr(merged, name)
                if not current:
                    setattr(merged, name, incoming)
                    # 首次出现也记一笔：这是"人设什么时候立起来"的锚点
                    timeline.record(chapter_index, name, incoming, "")
                    continue

                if incoming == current:
                    continue

                # 都有值且不同 → §7：保留较晚出现的，并把两者都记进 changes
                timeline.record(chapter_index, name, incoming, current)
                merged.changes.append(
                    PersonaChange(
                        field=name,
                        from_=current,
                        to=incoming,
                        reason=f"第 {chapter_index} 章的表述与已有画像不同，按 §7 保留较晚的并记录演化",
                    )
                )
                setattr(merged, name, incoming)

        # 列表字段：**无论文本字段是否为空**，一律保留（§7 要求"全部保留"）
        merged.relationships.extend(increment.relationships)
        merged.quotes.extend(_dedupe_quotes(increment.quotes, chapter_index))
        merged.speech_samples.extend(increment.speech_samples)
        merged.changes.extend(increment.changes)

    # §7：speech_samples 全部保留（去重、保持出现顺序）
    merged.speech_samples = list(dict.fromkeys(merged.speech_samples))
    # quotes 跨章去重：键同样是 (原始字段名, 原文)，**忽略 field 上的章节标记**
    deduped: list[Quote] = []
    seen: set[tuple[str, str]] = set()
    for quote in merged.quotes:
        key = (quote.field.split("@ch", 1)[0], quote.text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(quote)
    merged.quotes = deduped

    return MergedPersona(persona=merged, timeline=timeline)


def relationships_by_target(increment: PersonaIncrement) -> dict[str, list[str]]:
    """§7：`relationships` 按 `target` 聚合，同一 target 的多次变化全部保留成演化序列。"""
    grouped: dict[str, list[str]] = {}
    for relation in increment.relationships:
        if not relation.target and not relation.change:
            continue
        grouped.setdefault(relation.target, []).append(relation.change)
    return grouped
