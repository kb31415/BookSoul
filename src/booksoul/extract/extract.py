"""角色抽取（🔴 `MVP_PLAN.md` 阶段 4 —— MVP 第一风险点）。

目标：对指定人物抽出**结构化、有原文依据**的立体字段。

流程（`PROMPT_DESIGN.md` §5）：

```
人物相关段落 ──[Prompt 3: 逐章 persona 增量抽取]──→ 每章的 persona 增量
             ↓ 合并（§7，见 assemble/merge.py）
       最终 persona ──[Prompt 4: 开场白与原话示例生成]──→ first_mes + mes_example
```

三个关键点（都是设计明确要求的，不能省）：

1. **只把相关段落拼进 prompt**（§5 变量表 `{relevant_passages}`）——
   全文塞不下，而且会稀释注意力。**必须用主名 + 所有别名一起检索**，
   否则会漏掉大量段落（"沈师兄"指的就是"沈知舟"）。
2. **强制引用原文依据**（§5 质量约束 1 + §8）—— 没有原文支撑的字段宁可留空；
   quote 文本必须能在原文里找得到（§8 的"引用真实性"，把反幻觉变成可执行校验）。
3. **逐章增量**，已有画像喂回去，只抽"新增或改变"（§5 质量约束 4）——
   这样天然产出成长弧光。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from booksoul.assemble.merge import MergedPersona
from booksoul.assemble.persona import (
    PERSONA_TEXT_FIELDS,
    PersonaChange,
    PersonaIncrement,
    Quote,
    RelationshipChange,
)
from booksoul.assemble.validate import (
    DEFAULT_MAX_PASSAGE_CHARS,
    ValidationReport,
    validate_persona_increment,
)
from booksoul.ingest import Chapter, Novel
from booksoul.llm import LLMClient
from booksoul.prompts import render_prompt

__all__ = [
    "DEFAULT_MAX_PASSAGE_CHARS",
    "DEFAULT_PARAGRAPH_HINT",
    "CardFields",
    "ExtractionReport",
    "PersonaChange",
    "PersonaIncrement",
    "Quote",
    "RelationshipChange",
    "batch_passages",
    "build_persona_prompt",
    "empty_persona_summary",
    "extract_character",
    "extract_chapter_increments",
    "extract_persona_for_chapter",
    "format_persona_summary",
    "generate_card_fields",
    "parse_card_fields",
    "parse_persona_increment",
    "retrieve_passages",
    "score_passages",
    "split_paragraphs",
]

#: 单次 prompt 里塞的相关段落上限（`PROMPT_DESIGN.md` §5：单次建议不超过 5000 字）。
DEFAULT_MAX_PASSAGE_CHARS: int = 5000

#: 每章抽取失败后额外重试几次（`MVP_PLAN.md` 全局约定：失败重试一次）。
DEFAULT_VALIDATION_RETRIES: int = 1

#: 检索单元的粒度提示（超过它说明分段太粗，退到按单换行切）。
DEFAULT_PARAGRAPH_HINT: int = 1200


# ══════════════════════════ 检索相关段落（§5 变量表）══════════════════════════


def score_passages(passages: Iterable[str], names: Iterable[str]) -> list[str]:
    """挑出**含任一别名**的段落（关键词检索，不引 embedding）。

    段落按空行切（阶段 2 的 `clean_text` 保留了段落边界）。
    """
    needles = [name for name in names if name]
    if not needles:
        return []
    kept: list[str] = []
    for passage in passages:
        if any(name in passage for name in needles):
            kept.append(passage)
    return kept


#: 单段超过这个字数就认为"分段粒度太粗"，退到按单换行切（见 `split_paragraphs`）。
DEFAULT_PARAGRAPH_HINT: int = 1200


def split_paragraphs(content: str, *, max_block: int = DEFAULT_PARAGRAPH_HINT) -> list[str]:
    """把章节正文切成"检索单元"。

    先按**空行**切（现代网文的分段方式，阶段 2 的 `clean_text` 保留了空行）。
    但如果切出来的块远大于 `max_block`，说明这本书不是空行分段 ——
    古籍/一些盗版 txt 用**单换行**分段，此时"一段"可能是整章，
    检索命中一段就等于把整章喂给模型，人物必然串（实测踩过：
    《宛如约》某一章只有 2 段，命中 1 段 = 4583 字）。

    所以退一步按单换行切。返回非空块。
    """
    blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
    if not blocks:
        return []

    if max(len(block) for block in blocks) <= max_block:
        return blocks

    # 粒度太粗：按单换行切
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    return lines or blocks


def retrieve_passages(
    chapter: Chapter,
    names: Iterable[str],
    *,
    include_title_line: bool = False,
) -> list[str]:
    """取该章内与角色相关的段落。

    `include_title_line=False`（默认）时**只摘掉标题那一行文本**，段落剩下的正文照样保留 ——
    标题已经单独作为 `{chapter_title}` 传给模型了，重复喂一遍是浪费；
    但因为它和正文同处一段就把整段丢掉，会漏掉正文（踩过这个坑）。
    """
    paragraphs = split_paragraphs(chapter.content)

    if include_title_line or not chapter.title:
        return score_passages(paragraphs, names)

    title = chapter.title.strip()
    trimmed: list[str] = []
    for block in paragraphs:
        remaining = "\n".join(line for line in block.split("\n") if line.strip() != title).strip()
        if remaining:
            trimmed.append(remaining)

    return score_passages(trimmed, names)


def batch_passages(
    passages: list[str], max_chars: int = DEFAULT_MAX_PASSAGE_CHARS
) -> list[str]:
    """把相关段落按字数上限分批（§5：单次不超过 5000 字）。

    段内如果单段就超限，硬切（不丢内容）。返回每批拼好的文本。
    """
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正整数")
    if not passages:
        return []

    batches: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            batches.append("\n\n".join(current))
            current = []
            current_length = 0

    for passage in passages:
        # 单段超限：先冲掉手上的，再把它切开
        while len(passage) > max_chars:
            flush()
            batches.append(passage[:max_chars])
            passage = passage[max_chars:]

        addition = len(passage) + (2 if current else 0)
        if current and current_length + addition > max_chars:
            flush()
            addition = len(passage)
        current.append(passage)
        current_length += addition

    flush()
    return batches


# ══════════════════════════ Prompt 3 ══════════════════════════


#: `{existing_persona}` 里"关系变化"最多列几条（按章节顺序取**最近**的）。
#:
#: 为什么要设上限：这一整段会被拼进**每一次**逐章调用。早期实现把累积的全部
#: relationships 原文倒进去，于是每章 prompt 线性膨胀，总量 O(n²) ——
#: 实测《咎由自取》85 章：第 0 章 3,299 字符 → 第 19 章 19,420 字符，
#: 全量 prompt token 冲到 233 万（约 2.4 元/角色），是正常值的 11 倍。
#:
#: 设计本意（`PROMPT_DESIGN.md` §5）是"抽取本章**新增或改变**的信息"，
#: `{existing_persona}` 只该是**压缩摘要**，不是全量倾倒。
DEFAULT_RELATION_SUMMARY_LIMIT: int = 12

#: 摘要里每条关系变化最多保留多少字（超出截断）。
DEFAULT_RELATION_SUMMARY_CHARS: int = 40


def format_persona_summary(
    fields: dict[str, str],
    *,
    relationships: list[RelationshipChange] | None = None,
    changes: list[PersonaChange] | None = None,
    relation_limit: int = DEFAULT_RELATION_SUMMARY_LIMIT,
    relation_chars: int = DEFAULT_RELATION_SUMMARY_CHARS,
) -> str:
    """把已有画像整理成 `{existing_persona}` 的文本（§5 变量表）。

    **刻意做成压缩摘要**，不是全量倾倒（见 `DEFAULT_RELATION_SUMMARY_LIMIT` 的说明）：

    - 五个文本字段各一行（本身有长度约束，天然很小）
    - 关系变化**只列最近的 `relation_limit` 条**，每条截断到 `relation_chars`
    - `changes` 最多列 5 条（只是"提醒模型别重复"）
    """
    lines: list[str] = []
    labels = {
        "personality": "性格",
        "desire": "欲望",
        "flaw": "缺陷",
        "secret": "秘密",
        "speech_style": "说话风格",
    }
    for key, label in labels.items():
        value = fields.get(key, "")
        if value:
            lines.append(f"- {label}：{value}")

    items = [r for r in (relationships or []) if r.target or r.change]
    if items:
        # 取**最近的**若干条（后文更能体现最终状态），并压成一行一条
        recent = items[-relation_limit:] if relation_limit > 0 else []
        omitted = len(items) - len(recent)
        for relation in recent:
            change = relation.change.strip()
            if len(change) > relation_chars:
                change = change[:relation_chars] + "…"
            lines.append(f"- 与「{relation.target}」：{change}")
        if omitted > 0:
            lines.append(f"- （另有 {omitted} 条较早的关系变化已省略）")

    for change in (changes or [])[:5]:
        if change.field:
            lines.append(f"- 变化（{change.field}）：{change.from_} → {change.to}")

    return "\n".join(lines) if lines else "（无，这是首次抽取）"


def empty_persona_summary() -> str:
    """首次抽取时 `{existing_persona}` 的固定文案（§5 模板里写死的这句）。"""
    return "（无，这是首次抽取）"


def build_persona_prompt(
    character_name: str,
    aliases: list[str],
    chapter_index: int,
    chapter_title: str,
    existing_persona: str,
    relevant_passages: str,
) -> str:
    """Prompt 3 的完整文本（§5 模板 + 变量填充）。"""
    alias_text = "、".join(aliases) if aliases else "（无已知别名）"
    return render_prompt(
        "extract_persona",
        character_name=character_name,
        aliases=alias_text,
        chapter_index=chapter_index,
        chapter_title=chapter_title or "（无标题）",
        existing_persona=existing_persona or empty_persona_summary(),
        relevant_passages=relevant_passages,
    )


# ══════════════════════════ 输出解析 ══════════════════════════


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def parse_persona_increment(raw: Any) -> PersonaIncrement:
    """把 Prompt 3 的 JSON 解析成 `PersonaIncrement`。

    宽容处理两种常见偏差：`speech_samples` / `relationships` 给了字符串而不是数组。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"persona 增量必须是 dict，收到 {type(raw).__name__}")

    payload = dict(raw)
    payload["speech_samples"] = _as_str_list(payload.get("speech_samples"))

    relationships = payload.get("relationships")
    if isinstance(relationships, str):
        payload["relationships"] = (
            [{"target": "", "change": relationships}] if relationships.strip() else []
        )
    elif isinstance(relationships, dict):
        payload["relationships"] = [relationships]

    for key in ("quotes", "changes"):
        if isinstance(payload.get(key), dict):
            payload[key] = [payload[key]]

    return PersonaIncrement.model_validate(payload)


# ══════════════════════════ 单章抽取 ══════════════════════════


@dataclass
class _ChapterOutcome:
    """一章（可能多批）的抽取结果 + 校验报告。"""

    increment: PersonaIncrement
    report: ValidationReport


def _merge_increments(increments: list[PersonaIncrement]) -> PersonaIncrement:
    """同一章多批次的合并：文本字段取第一个非空，列表字段**去重**后拼接。

    为什么文本取第一个非空而不是覆盖：分批是**按段落位置**切的，同一章内
    人设本身没有先后演进关系，先抽到的批次不代表更早。

    为什么列表要去重：模型在不同批次里会重复输出同一条关系变化 —— 实测出现过
    同一条重复 14 次（`relationships` 序列被撑爆、CLI 输出刷屏）。
    与 `assemble.merge` 的跨章去重保持一致的键。
    """
    if not increments:
        return PersonaIncrement()
    if len(increments) == 1:
        return increments[0]

    merged: dict[str, Any] = {}
    for name in PERSONA_TEXT_FIELDS:
        merged[name] = next(
            (getattr(item, name) for item in increments if getattr(item, name)), ""
        )

    quotes: list[Quote] = []
    samples: list[str] = []
    relationships: list[RelationshipChange] = []
    changes: list[PersonaChange] = []

    seen_quotes: set[tuple[str, str]] = set()
    seen_relations: set[tuple[str, str]] = set()
    seen_changes: set[tuple[str, str, str]] = set()

    for item in increments:
        for quote in item.quotes:
            key = (quote.field, quote.text)
            if key not in seen_quotes:
                seen_quotes.add(key)
                quotes.append(quote)
        samples.extend(item.speech_samples)
        for relation in item.relationships:
            key = (relation.target, relation.change)
            if key not in seen_relations:
                seen_relations.add(key)
                relationships.append(relation)
        for change in item.changes:
            key = (change.field, change.from_, change.to)
            if key not in seen_changes:
                seen_changes.add(key)
                changes.append(change)

    return PersonaIncrement(
        **merged,
        quotes=quotes,
        speech_samples=list(dict.fromkeys(samples)),
        relationships=relationships,
        changes=changes,
    )


def extract_persona_for_chapter(
    client: LLMClient,
    character_name: str,
    aliases: list[str],
    chapter: Chapter,
    *,
    existing_persona: str = "",
    max_passage_chars: int = DEFAULT_MAX_PASSAGE_CHARS,
    include_title_line: bool = False,
) -> _ChapterOutcome:
    """用 Prompt 3 抽一章的 persona 增量（段落超限时分批调用再合并）。

    每批都独立过一遍 §8 的确定性校验；把各批的告警合并返回。
    """
    passages = retrieve_passages(chapter, [character_name, *aliases], include_title_line=include_title_line)
    batches = batch_passages(passages, max_chars=max_passage_chars)

    if not batches:
        return _ChapterOutcome(PersonaIncrement(), ValidationReport())

    increments: list[PersonaIncrement] = []
    reports: list[ValidationReport] = []

    for batch in batches:
        prompt = build_persona_prompt(
            character_name=character_name,
            aliases=aliases,
            chapter_index=chapter.index,
            chapter_title=chapter.title,
            existing_persona=existing_persona,
            relevant_passages=batch,
        )
        response = client.complete("", prompt, json_mode=True)
        increment = parse_persona_increment(response.json())
        increments.append(increment)
        reports.append(
            validate_persona_increment(
                increment,
                source_text=chapter.content,
                chapter_index=chapter.index,
            )
        )

    return _ChapterOutcome(_merge_increments(increments), ValidationReport.merge(reports))


# ══════════════════════════ 逐章抽取主流程 ══════════════════════════


def _extend_unique(target: list[Any], values: Iterable[Any]) -> None:
    """把 `values` 里**还没出现过**的项追加到 `target`（保持顺序）。"""
    for value in values:
        if value not in target:
            target.append(value)


@dataclass
class ExtractionReport:
    """一次角色抽取的汇总（CLI 打印 & 人工检查用）。

    字段都是**列表而不是计数**：人工把关时要看到"具体哪些字段被置空、哪些引用被丢弃"，
    只给一个数字没法排查（`MVP_PLAN.md` 阶段 4 是人工把关点）。
    """

    character_name: str
    chapter_total: int = 0
    chapter_hit: int = 0
    chapter_empty: int = 0
    retried_chapters: list[int] = field(default_factory=list)
    dropped_quotes: list[str] = field(default_factory=list)
    cleared_fields: list[str] = field(default_factory=list)
    ungrounded_fields: list[str] = field(default_factory=list)
    cliches: list[str] = field(default_factory=list)
    field_length_warnings: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_problems(self) -> bool:
        return bool(
            self.dropped_quotes
            or self.cleared_fields
            or self.ungrounded_fields
            or self.cliches
            or self.field_length_warnings
            or self.retried_chapters
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "character_name": self.character_name,
            "chapter_total": self.chapter_total,
            "chapter_hit": self.chapter_hit,
            "chapter_empty": self.chapter_empty,
            "retried_chapters": list(self.retried_chapters),
            "dropped_quotes": list(self.dropped_quotes),
            "cleared_fields": list(self.cleared_fields),
            "ungrounded_fields": list(self.ungrounded_fields),
            "cliches": list(self.cliches),
            "field_length_warnings": list(self.field_length_warnings),
            "usage": dict(self.usage),
        }


def extract_chapter_increments(
    novel: Novel,
    client: LLMClient,
    character_name: str,
    aliases: list[str] | None = None,
    *,
    max_passage_chars: int = DEFAULT_MAX_PASSAGE_CHARS,
    retries: int = DEFAULT_VALIDATION_RETRIES,
    include_title_line: bool = False,
) -> tuple[list[PersonaIncrement], ExtractionReport]:
    """逐章抽 persona 增量，**把已有画像喂回给下一章**（§5 逐章增量）。

    返回 `(每章一个增量, 汇总报告)`；增量与 `novel.chapters` 一一对应
    （没抽到内容的章是空增量，保证下标对齐）。
    """
    from booksoul.assemble.merge import merge_persona_increments

    alias_list = list(aliases or [])
    increments: list[PersonaIncrement] = []
    report = ExtractionReport(character_name=character_name, chapter_total=len(novel.chapters))

    accumulated = PersonaIncrement()

    for chapter in novel.chapters:
        existing = format_persona_summary(
            accumulated.text_values(),
            relationships=accumulated.relationships,
            changes=accumulated.changes,
        )

        outcome = _extract_with_retry(
            client,
            character_name,
            alias_list,
            chapter,
            existing_persona=existing,
            max_passage_chars=max_passage_chars,
            retries=retries,
            include_title_line=include_title_line,
        )

        if outcome is None:  # 该章没有相关段落
            increments.append(PersonaIncrement())
            report.chapter_empty += 1
            continue

        increments.append(outcome.increment)
        report.chapter_hit += 1
        # 逐章累积时也去重：同一类告警（如"flaw: 26 字"）会在几十章里重复出现，
        # 全量堆进报告只会把真正的问题淹掉（实测累积到 212 条、大量是重复文本）。
        _extend_unique(report.dropped_quotes, outcome.report.dropped_quotes)
        _extend_unique(report.cleared_fields, outcome.report.cleared_fields)
        _extend_unique(report.ungrounded_fields, outcome.report.ungrounded_fields)
        _extend_unique(report.cliches, outcome.report.cliches)
        _extend_unique(report.field_length_warnings, outcome.report.field_length_warnings)
        _extend_unique(report.retried_chapters, outcome.report.retried_chapters or [])

        accumulated = merge_persona_increments([accumulated, outcome.increment]).persona

    report.usage = client.usage.as_dict()
    return increments, report


def _extract_with_retry(
    client: LLMClient,
    character_name: str,
    aliases: list[str],
    chapter: Chapter,
    *,
    existing_persona: str,
    max_passage_chars: int,
    retries: int,
    include_title_line: bool,
) -> _ChapterOutcome | None:
    """调用 + Pydantic 校验；校验失败按 `retries` 重试一次，仍失败**抛错退出**。

    为什么把"重试"和"报错"都做进这里：`MVP_PLAN.md` 全局约定是
    「Pydantic 校验 + 失败重试一次，仍失败则报错退出（不静默吞掉）」——
    静默产出残缺 persona 是这个风险点最危险的失败方式（看起来成功了）。
    """
    if not retrieve_passages(chapter, [character_name, *aliases], include_title_line=include_title_line):
        return None

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return extract_persona_for_chapter(
                client,
                character_name,
                aliases,
                chapter,
                existing_persona=existing_persona,
                max_passage_chars=max_passage_chars,
                include_title_line=include_title_line,
            )
        except (ValidationError, ValueError, KeyError) as exc:
            last_error = exc
            if attempt < retries:
                continue

    raise RuntimeError(
        f"第 {chapter.index} 章「{chapter.title}」的 persona 增量连续 "
        f"{retries + 1} 次无法解析/校验：{type(last_error).__name__}: {last_error}"
    )


def extract_character(
    novel: Novel,
    client: LLMClient,
    character_name: str,
    aliases: list[str] | None = None,
    **kwargs: Any,
) -> tuple[MergedPersona, ExtractionReport]:
    """抽一个角色的最终 persona（逐章增量 + 按 §7 规则合并）。

    返回 `(合并结果[含时间线], 汇总报告)`。
    """
    from booksoul.assemble.merge import merge_persona_increments

    increments, report = extract_chapter_increments(
        novel, client, character_name, aliases, **kwargs
    )
    return merge_persona_increments(increments), report


# ══════════════════════════ Prompt 4 ══════════════════════════


class CardFields(BaseModel):
    """Prompt 4 的产物（`PROMPT_DESIGN.md` §6 输出格式）。"""

    model_config = ConfigDict(extra="ignore")

    first_mes: str = ""
    mes_example: str = ""


def parse_card_fields(raw: Any) -> CardFields:
    if not isinstance(raw, dict):
        raise ValueError(f"CardFields 必须是 dict，收到 {type(raw).__name__}")
    return CardFields.model_validate(raw)


def generate_card_fields(
    client: LLMClient,
    character_name: str,
    final_persona: PersonaIncrement,
    *,
    max_samples: int = 12,
    retries: int = DEFAULT_VALIDATION_RETRIES,
) -> CardFields:
    """用 Prompt 4 生成 `first_mes` + `mes_example`（§6）。

    原话素材取合并后的 `speech_samples`（§7：全部保留，最终由 Prompt 4 挑选）。
    """
    samples = final_persona.speech_samples[:max_samples]
    sample_text = "\n".join(f"- {sample}" for sample in samples) or "（书中没有可用的原话素材）"

    prompt = render_prompt(
        "generate_card_fields",
        final_persona=format_persona_summary(
            final_persona.text_values(),
            relationships=final_persona.relationships,
            changes=final_persona.changes,
        ),
        speech_samples=sample_text,
    )

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.complete("", prompt, json_mode=True)
            return parse_card_fields(response.json())
        except (ValidationError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                continue

    raise RuntimeError(
        f"「{character_name}」的 first_mes/mes_example 连续 {retries + 1} 次无法解析："
        f"{type(last_error).__name__}: {last_error}"
    )
