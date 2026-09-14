"""Persona 质量校验（`PROMPT_DESIGN.md` §8 的检查清单，**确定性校验，不依赖 LLM**）。

§8 那张表是逐条实现的：

| 检查 | 规则 | 不通过怎么办 |
|---|---|---|
| 引用完整性 | 每个非空字段都必须有对应的 `quotes` 条目 | 该字段置空 |
| 引用真实性 | `quotes[].text` 必须能在原文中找到（字符串匹配，允许忽略标点） | 丢弃该条 quote 并告警 |
| 套话检测 | 命中黑名单（"性格复杂""内心矛盾""多面的""很有魅力"等） | 告警，人工复核 |
| 长度约束 | 各字段字数在定义范围内 | 告警 |
| JSON 合法性 | 能被 Pydantic 解析 | 重试一次，再失败报错退出 |

**「引用真实性」是最有价值的一项** —— 它把"反幻觉"从口号变成可执行的校验。

一处设计留白（已按最保守方式实现）：§8 说的是"该字段置空"，但没说清要区分
"LLM 根本没给这条引用"和"给的引用是假的"。这里按**动作**分流 ——

- 引用缺失 → **字段置空**（§8 字面要求）
- 引用存在但对不上原文 → **丢弃该条 quote + 告警**（§8 对 quote 的要求），
  字段本身保留但**标记为无依据**，交给阶段 4 的人工把关判断

另外提供 `strict=True`：字段无有效引用就置空（阶段 5 若要求"每条结论都可溯源"可开）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from booksoul.assemble.persona import PersonaIncrement

__all__ = [
    "CLICHE_BLACKLIST",
    "DEFAULT_FIELD_LENGTHS",
    "DEFAULT_MAX_PASSAGE_CHARS",
    "ValidationReport",
    "clean_for_matching",
    "find_cliches",
    "quote_is_grounded",
    "validate_persona_increment",
]

#: 单次 prompt 里相关段落的上限（`PROMPT_DESIGN.md` §5）。
DEFAULT_MAX_PASSAGE_CHARS: int = 5000

#: 套话黑名单（§8；§5 质量约束 2 也点名了几个）。
CLICHE_BLACKLIST: tuple[str, ...] = (
    "性格复杂",
    "内心矛盾",
    "很有魅力",
    "多面性",
    "多面的",
    "复杂多面",
    "深不可测",
    "神秘莫测",
    "独一无二",
    "与众不同",
    "有着复杂的内心",
    "既有温柔的一面也有冷漠的一面",
)

#: 各字段字数范围（§5 字段定义）。超出只告警，不置空。
DEFAULT_FIELD_LENGTHS: dict[str, tuple[int, int]] = {
    "personality": (15, 40),
    "desire": (10, 25),
    "flaw": (10, 25),
    "secret": (10, 30),
    "speech_style": (10, 25),
}

#: 比较引文时忽略的字符（标点 + 空白）—— §8：「允许忽略标点」。
_IGNORED_IN_QUOTE = re.compile(r"[\s，。、；：？！“”‘’（）《》〈〉「」『』…—·,.;:?!\"'()\[\]<>~-]+")


def clean_for_matching(text: str) -> str:
    """归一化文本用于引用比对：去掉标点与空白。

    这样 `「沈知舟立在廊下。」` 与 `沈知舟立在廊下` 能匹配上。
    """
    return _IGNORED_IN_QUOTE.sub("", text or "")


def quote_is_grounded(quote: str, source_text: str) -> bool:
    """这条引用真的能在原文里找到吗（§8 的「引用真实性」）。

    允许忽略标点；空引用一律算"不成立"。
    """
    needle = clean_for_matching(quote)
    if not needle:
        return False
    return needle in clean_for_matching(source_text)


def find_cliches(increment: PersonaIncrement) -> list[str]:
    """找出命中套话黑名单的字段（§8：告警，人工复核）。"""
    hits: list[str] = []
    for name, value in increment.text_values().items():
        for cliche in CLICHE_BLACKLIST:
            if cliche in value:
                hits.append(f"{name}: 命中套话「{cliche}」")
                break
    for change in increment.relationships:
        for cliche in CLICHE_BLACKLIST:
            if cliche in change.change:
                hits.append(f"relationships[{change.target}]: 命中套话「{cliche}」")
                break
    return hits


@dataclass
class ValidationReport:
    """一章的校验结果（全部是**告警**，不是异常 —— 异常只有 JSON 解析失败）。"""

    chapter_index: int | None = None
    cleared_fields: list[str] = field(default_factory=list)
    dropped_quotes: list[str] = field(default_factory=list)
    ungrounded_fields: list[str] = field(default_factory=list)
    cliches: list[str] = field(default_factory=list)
    field_length_warnings: list[str] = field(default_factory=list)
    retried_chapters: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.cleared_fields
            or self.dropped_quotes
            or self.ungrounded_fields
            or self.cliches
            or self.field_length_warnings
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.cleared_fields:
            parts.append(f"置空 {len(self.cleared_fields)} 个无引用字段")
        if self.dropped_quotes:
            parts.append(f"丢弃 {len(self.dropped_quotes)} 条假引用")
        if self.ungrounded_fields:
            parts.append(f"{len(self.ungrounded_fields)} 个字段引用未落实")
        if self.cliches:
            parts.append(f"{len(self.cliches)} 处套话")
        if self.field_length_warnings:
            parts.append(f"{len(self.field_length_warnings)} 处长度越界")
        return "、".join(parts) if parts else "通过"

    @classmethod
    def merge(cls, reports: list[ValidationReport]) -> ValidationReport:
        """把同一章多个批次的报告合并成一个（**逐项去重**）。

        为什么去重：同一章的多个批次几乎总会产出**完全一样**的告警
        （例如五个字段都超长 → 每批都报一遍）。实测 85 章抽完，
        `field_length_warnings` 累积到 212 条、其中大量是重复文本，
        把报告刷成噪声，人工根本看不出真正的问题。
        """
        merged = cls(chapter_index=reports[0].chapter_index if reports else None)
        for report in reports:
            for target, values in (
                (merged.cleared_fields, report.cleared_fields),
                (merged.dropped_quotes, report.dropped_quotes),
                (merged.ungrounded_fields, report.ungrounded_fields),
                (merged.cliches, report.cliches),
                (merged.field_length_warnings, report.field_length_warnings),
            ):
                target.extend(value for value in values if value not in target)
            merged.retried_chapters.extend(
                index for index in report.retried_chapters if index not in merged.retried_chapters
            )
        return merged

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_index": self.chapter_index,
            "cleared_fields": list(self.cleared_fields),
            "dropped_quotes": list(self.dropped_quotes),
            "ungrounded_fields": list(self.ungrounded_fields),
            "cliches": list(self.cliches),
            "field_length_warnings": list(self.field_length_warnings),
        }


def validate_persona_increment(
    increment: PersonaIncrement,
    *,
    source_text: str,
    chapter_index: int | None = None,
    strict: bool = False,
) -> ValidationReport:
    """按 §8 的清单校验一章的增量，并**就地修正** `increment`。

    返回校验报告；`increment` 会被修正为：

    - 字段完全没有引用 → 置空（§8「引用完整性」）
    - 引用对不上原文 → 从 `quotes` 里移除（§8「引用真实性」）
    - `strict=True` 时，字段的引用全被丢弃 → 该字段也置空
    """
    report = ValidationReport(chapter_index=chapter_index)

    # ① 引用真实性：丢掉对不上原文的 quote
    grounded: list[Any] = []
    for quote in increment.quotes:
        if quote_is_grounded(quote.text, source_text):
            grounded.append(quote)
        else:
            report.dropped_quotes.append(f"{quote.field}: {quote.text[:40]}")

    # ② 套话检测（先检测再改，避免置空后漏报）
    report.cliches.extend(find_cliches(increment))

    # ③ 长度约束
    for name, value in increment.text_values().items():
        if not value.strip():
            continue
        low, high = DEFAULT_FIELD_LENGTHS.get(name, (0, 10**6))
        if not (low <= len(value) <= high):
            report.field_length_warnings.append(
                f"{name}: {len(value)} 字（要求 {low}–{high}）"
            )

    # ④ 引用完整性（§8）：**每个非空字段都必须有对应的 quotes 条目**，否则置空。
    #
    #    实现上把情况分清（避免把"没写引用"和"引用是假的"混为一谈）：
    #    - `offered_fields`：LLM **声明过**要支撑哪些字段（不管引用真假）
    #    - `fields_with_quotes`：真的过了引用真实性校验的字段
    #
    #    · 某字段**没有**对应 quote 条目 → 置空（§8 表格的字面要求）
    #    · 某字段**有**条目但引用被证伪 → 非严格模式保留 + 标记未落实（交人工把关）；
    #      `strict=True` 时同样置空
    #
    #    匹配的是**规范化后的字段名**：合并时（§7）会把章节号附在 field 上
    #    （`personality@ch2`），比对前必须剥掉，否则"有引用的字段"会被误判成无依据。
    offered_fields = {quote.field.split("@ch", 1)[0] for quote in increment.quotes}
    fields_with_quotes = {quote.field.split("@ch", 1)[0] for quote in grounded}

    for name in increment.non_empty_fields():
        if name in fields_with_quotes:
            continue  # 有可溯源引用，正常
        if name not in offered_fields or strict:
            report.cleared_fields.append(name)
            setattr(increment, name, "")
        else:
            report.ungrounded_fields.append(name)

    increment.quotes = grounded
    return report
