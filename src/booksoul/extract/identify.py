"""角色识别（粗扫）—— `MVP_PLAN.md` 阶段 3。

目标：找出主要人物候选列表。

流程（`PROMPT_DESIGN.md` §1 的前两步）：

```
逐章文本 ──[Prompt 1: 人名抽取]──→ 每章人名
             ↓ 汇总（出现章数 + 出现次数）
       候选人物 ──[Prompt 2: 别名归并]──→ 人物组（主名 + 别名）
```

三条硬约束：

1. **逐章调用，结果按章落盘**（`data/cards/{book_id}/chapters/{i}.names.json`）——
   重跑不烧 token（`MVP_PLAN.md` §1 全局约定）。
2. **单章超过 6000 字要再切块**（`PROMPT_DESIGN.md` §3 建议）。一章多块时
   文件名仍按**原章号** `{i}`，块装在同一个文件里；汇总时把各块名字合并计数。
3. **用便宜模型**（这一环节量大、任务简单）。

Prompt 原文照搬 `PROMPT_DESIGN.md` §3 / §4 —— 不改写（`HANDOFF.md` §0：
设计已定稿，实现方不重新设计）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from booksoul.ingest import Chapter, Novel, split_text
from booksoul.llm import LLMClient, LLMUsage
from booksoul.storage import ChapterNameCache, RepositorySet

__all__ = [
    "ALIAS_SYSTEM_PROMPT",
    "DEFAULT_CHAPTER_CHAR_LIMIT",
    "DEFAULT_TOP_N",
    "NAME_SYSTEM_PROMPT",
    "Candidate",
    "CandidateList",
    "ChapterScanResult",
    "CharacterGroup",
    "IdentifyReport",
    "build_alias_prompt",
    "build_name_prompt",
    "chapter_cache_fingerprint",
    "identify_candidates",
    "identify_chapters",
    "merge_aliases",
    "parse_alias_groups",
    "parse_names",
    "rank_candidates",
    "sample_contexts",
    "scan_chapter",
]

#: 单章超过这个字数就再切块（`PROMPT_DESIGN.md` §3 建议值）。
DEFAULT_CHAPTER_CHAR_LIMIT: int = 6000

#: 取前 N 作为候选（`PROMPT_DESIGN.md` §3 建议 10–20）。
DEFAULT_TOP_N: int = 15

#: 每个候选名称抽几句原文上下文（§4：2–3 句，上下文是判断的唯一依据）。
CONTEXT_SENTENCES: int = 3


# ══════════════════════════ Prompt 原文（照搬设计）══════════════════════════

#: `PROMPT_DESIGN.md` §3 Prompt 1 的固定部分。
NAME_SYSTEM_PROMPT = """你是中文小说人物识别专家。

【任务】
从下面的章节文本中，提取所有出现的**人物名称**。

【规则】
1. 只提取人物（角色），不提取地名、门派、组织、物品、动物（拟人化角色除外）。
2. 包括：本名、小名、绰号、尊称（如"沈师兄"）、亲属称谓（如"王婆婆"）。
3. 不提取泛称：如"众人""路人""一个男人""那女子"。
4. 同一章中重复出现的名字只输出一次。
5. 只输出 JSON，不要任何解释文字。

【输出格式】
{"characters": ["沈知舟", "沈师兄", "林晚", "王婆婆"]}"""

#: `PROMPT_DESIGN.md` §4 Prompt 2 的固定部分（`{name_contexts}` 由调用方拼）。
ALIAS_SYSTEM_PROMPT = """你是中文小说人物分析专家。

【任务】
判断下面这些名称中，哪些**指代同一个人物**。

【规则】
1. **只有在证据充分时才合并**（如文中明确"沈师兄"就是"沈知舟"）。
2. 不同人物可能同姓，**不要因为姓氏相同就合并**。
3. 拿不准就不要合并——**宁可漏合并，不可错合并**。
4. 每组给一个主名（取最完整的全名），其余作为别名。
5. 只输出 JSON，不要任何解释文字。

【输出格式】
{"groups": [
  {"main": "沈知舟", "aliases": ["沈师兄", "知舟"]},
  {"main": "林晚", "aliases": ["晚晚"], "reason": "第7章：林晚小名晚晚"}
]}"""


def build_name_prompt(chapter_title: str, chapter_text: str) -> str:
    """Prompt 1 的 user 部分：把章节标题与正文填进模板（§3）。"""
    return (
        "【章节标题】"
        f"{chapter_title or '（无标题）'}\n"
        "【章节文本】\n"
        f"{chapter_text}"
    )


def build_alias_prompt(name_contexts: str) -> str:
    """Prompt 2 的 user 部分：候选名称及其上下文（§4）。"""
    return f"【候选名称及上下文】\n{name_contexts}"


# ══════════════════════════ 输出解析 ══════════════════════════


class _NamesPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    characters: list[str] = Field(default_factory=list)


class _AliasGroupPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    main: str = ""
    aliases: list[str] = Field(default_factory=list)
    reason: str = ""


class _AliasPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    groups: list[_AliasGroupPayload] = Field(default_factory=list)


def _clean_name(value: Any) -> str:
    """收敛一个名字：去空白、去包裹符号（LLM 常带回引号或书名号）。"""
    if not isinstance(value, str):
        return ""
    return value.strip().strip("「」『』\"'《》【】（）()").strip()


def parse_names(raw: Any) -> list[str]:
    """把 Prompt 1 的 JSON 结果解析成去重后的名字列表。

    宽容一点：`{"characters": [...]}` 是规定格式，但也接受裸数组 / `names` 键。
    """
    items: Any = raw
    if isinstance(raw, dict):
        for key in ("characters", "names", "人物", "characters_list"):
            if key in raw:
                items = raw[key]
                break

    if not isinstance(items, (list, tuple)):
        return []

    seen: dict[str, None] = {}
    for item in items:
        name = _clean_name(item)
        if name:
            seen.setdefault(name, None)
    return list(seen)


def parse_alias_groups(raw: Any) -> list[_AliasGroupPayload]:
    """把 Prompt 2 的 JSON 结果解析成人物组，并做安全校验（§4 规则 3）。"""
    items: Any = raw
    if isinstance(raw, dict):
        items = raw.get("groups", [])
    if not isinstance(items, (list, tuple)):
        return []

    groups: list[_AliasGroupPayload] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        main = _clean_name(item.get("main", ""))
        if not main:
            continue
        aliases = [name for name in (_clean_name(a) for a in item.get("aliases", []) or []) if name]
        # 主名自己不能同时是别名
        aliases = [alias for alias in aliases if alias != main]
        groups.append(
            _AliasGroupPayload(
                main=main,
                aliases=list(dict.fromkeys(aliases)),
                reason=str(item.get("reason", "") or ""),
            )
        )
    return groups


# ══════════════════════════ 结果契约 ══════════════════════════


class Candidate(BaseModel):
    """一个候选人物（喂给阶段 4 精抽的输入）。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    chapter_count: int = 0
    mentions: int = 0
    chapter_indexes: list[int] = Field(default_factory=list)
    contexts: list[str] = Field(default_factory=list)


class CandidateList(BaseModel):
    """候选人物列表（落盘 `data/cards/{book_id}/candidates.json`）。"""

    model_config = ConfigDict(extra="ignore")

    book_id: str
    model: str = ""
    chapter_total: int = 0
    chapter_scanned: int = 0
    candidates: list[Candidate] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)

    def names(self) -> list[str]:
        return [candidate.name for candidate in self.candidates]


class CharacterGroup(BaseModel):
    """别名归并后的人物组（Prompt 2 的产物）。"""

    model_config = ConfigDict(extra="ignore")

    main: str
    aliases: list[str] = Field(default_factory=list)
    reason: str = ""

    def all_names(self) -> list[str]:
        return [self.main, *self.aliases]


@dataclass
class ChapterScanResult:
    """一章的识别结果。"""

    chapter_index: int
    names: list[str] = field(default_factory=list)
    from_cache: bool = False


@dataclass
class IdentifyReport:
    """一次阶段 3 跑完的汇总（CLI 打印 & 人工检查用）。"""

    book_id: str
    chapter_total: int = 0
    chapter_cached: int = 0
    chapter_scanned: int = 0
    candidate_count: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    candidates_path: str = ""


# ══════════════════════════ 逐章识别 ══════════════════════════


def chapter_cache_fingerprint(chapter: Chapter, chunks: list[str]) -> str:
    """缓存指纹：标题 + 各块正文。正文变了就重跑，没变就跳过。"""
    digest = hashlib.sha1()
    digest.update(chapter.title.encode("utf-8"))
    for chunk in chunks:
        digest.update(b"\x00")
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()[:16]


def _chunk_chapter(chapter: Chapter, char_limit: int) -> list[str]:
    """把一章切成若干块（不超 `char_limit`）。只需一块时原样返回。"""
    if char_limit <= 0:
        raise ValueError("char_limit 必须为正整数")
    if len(chapter.content) <= char_limit:
        return [chapter.content]
    return [piece.content for piece in split_text(chapter.content, length=char_limit, title_prefix="")]


def scan_chapter(
    client: LLMClient,
    chapter: Chapter,
    *,
    char_limit: int = DEFAULT_CHAPTER_CHAR_LIMIT,
) -> ChapterNameCache:
    """用 Prompt 1 扫一章（必要时分块），返回可落盘的缓存对象。

    每块单独调用一次；一块失败就抛 `LLMCallError`（不静默吞掉，
    见 `MVP_PLAN.md` §1 全局约定）。
    """
    chunks = _chunk_chapter(chapter, char_limit)
    results: list[list[str]] = []
    for chunk in chunks:
        response = client.complete(
            NAME_SYSTEM_PROMPT,
            build_name_prompt(chapter.title, chunk),
            json_mode=True,
        )
        try:
            results.append(parse_names(response.json()))
        except (ValueError, TypeError):
            # JSON 解析不了：这一块当"没识别出人名"，但不能假装成功 —— 记进指纹之外的日志
            results.append([])
            client.usage.add(None, failed=True)

    return ChapterNameCache.from_chunks(
        chapter_index=chapter.index,
        chunks=results,
        fingerprint=chapter_cache_fingerprint(chapter, chunks),
    )


def identify_chapters(
    novel: Novel,
    client: LLMClient,
    repositories: RepositorySet,
    *,
    char_limit: int = DEFAULT_CHAPTER_CHAR_LIMIT,
    force: bool = False,
) -> tuple[list[ChapterNameCache], IdentifyReport]:
    """逐章识别，命中缓存就跳过（`force=True` 强制重跑）。

    返回 `(按章号升序的缓存列表, 汇总报告)`。
    """
    cached = repositories.cards.load_all_chapter_names(novel.book_id)
    caches: list[ChapterNameCache] = []
    report = IdentifyReport(book_id=novel.book_id, chapter_total=len(novel.chapters))

    for chapter in novel.chapters:
        existing = cached.get(chapter.index)
        chunks = _chunk_chapter(chapter, char_limit)
        fingerprint = chapter_cache_fingerprint(chapter, chunks)

        if not force and existing is not None and existing.fingerprint == fingerprint:
            caches.append(existing)
            report.chapter_cached += 1
            continue

        fresh = scan_chapter(client, chapter, char_limit=char_limit)
        repositories.cards.save_chapter_names(novel.book_id, fresh)
        caches.append(fresh)
        report.chapter_scanned += 1

    report.usage = client.usage.as_dict()
    return caches, report


# ══════════════════════════ 汇总与排序 ══════════════════════════


def sample_contexts(name: str, chapters: list[Chapter], limit: int = CONTEXT_SENTENCES) -> list[str]:
    """抽几句含该名字的原文（`PROMPT_DESIGN.md` §4：上下文是判断的唯一依据）。"""
    pattern = re.compile(r"[^。！？!?\n]*" + re.escape(name) + r"[^。！？!?\n]*[。！？!?]?")
    found: list[str] = []
    for chapter in chapters:
        for match in pattern.finditer(chapter.content):
            sentence = match.group(0).strip()
            if len(sentence) < 4:
                continue
            found.append(sentence)
            if len(found) >= limit:
                return found
    return found


def rank_candidates(
    caches: list[ChapterNameCache],
    chapters: list[Chapter],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> list[Candidate]:
    """按 `PROMPT_DESIGN.md` §3：**出现章数 + 出现次数**排序，取前 N。

    并列时用名字本身做稳定排序（保证同样输入永远同样输出）。
    """
    chapters_by_index = {chapter.index: chapter for chapter in chapters}

    chapter_hits: dict[str, set[int]] = {}
    mention_totals: dict[str, int] = {}

    for cache in caches:
        for chunk in cache.chunks:
            counts: dict[str, int] = {}
            for name in chunk:
                counts[name] = counts.get(name, 0) + 1
            for name, count in counts.items():
                chapter_hits.setdefault(name, set()).add(cache.chapter_index)
                mention_totals[name] = mention_totals.get(name, 0) + count

    ordered = sorted(
        mention_totals,
        key=lambda name: (-len(chapter_hits.get(name, set())), -mention_totals[name], name),
    )

    candidates: list[Candidate] = []
    for name in ordered[:top_n]:
        indexes = sorted(chapter_hits.get(name, set()))
        relevant = [chapters_by_index[i] for i in indexes if i in chapters_by_index]
        candidates.append(
            Candidate(
                name=name,
                chapter_count=len(indexes),
                mentions=mention_totals[name],
                chapter_indexes=indexes,
                contexts=sample_contexts(name, relevant),
            )
        )
    return candidates


def identify_candidates(
    novel: Novel,
    client: LLMClient,
    repositories: RepositorySet,
    *,
    char_limit: int = DEFAULT_CHAPTER_CHAR_LIMIT,
    top_n: int = DEFAULT_TOP_N,
    force: bool = False,
) -> tuple[CandidateList, IdentifyReport]:
    """阶段 3 主流程：逐章识别 → 汇总排序 → 落盘 `candidates.json`。"""
    caches, report = identify_chapters(
        novel, client, repositories, char_limit=char_limit, force=force
    )
    candidates = rank_candidates(caches, novel.chapters, top_n=top_n)

    payload = CandidateList(
        book_id=novel.book_id,
        model=client.model,
        chapter_total=len(novel.chapters),
        chapter_scanned=report.chapter_scanned,
        candidates=candidates,
        usage=client.usage.as_dict(),
    )
    repositories.cards.save_json(
        novel.book_id, "candidates", payload.model_dump(mode="json")
    )
    # 别名归并要用上下文；把它也落盘，方便人工检查与单独重跑归并
    repositories.cards.save_json(
        novel.book_id,
        "name_contexts",
        {candidate.name: candidate.contexts for candidate in candidates},
    )

    report.candidate_count = len(candidates)
    report.usage = client.usage.as_dict()
    return payload, report


# ══════════════════════════ 别名归并（Prompt 2）══════════════════════════


def merge_aliases(
    client: LLMClient,
    name_contexts: dict[str, list[str]],
) -> list[CharacterGroup]:
    """用 Prompt 2 判断哪些名字指同一个人物（§4）。

    `name_contexts` 的格式：`{名字: [含该名字的原文句子, ...]}`。
    """
    if not name_contexts:
        return []

    lines: list[str] = []
    for name, contexts in name_contexts.items():
        marks = "".join(f"{chr(0x2460 + index)}{quote}" for index, quote in enumerate(contexts[:3]))
        lines.append(f"- {name}：{marks or '（无上下文）'}")
    prompt = build_alias_prompt("\n".join(lines))

    response = client.complete(ALIAS_SYSTEM_PROMPT, prompt, json_mode=True)
    try:
        groups = parse_alias_groups(response.json())
    except (ValueError, TypeError):
        return []

    # 只保留真正发生了合并的组（单名成组对下游没意义）
    return [
        CharacterGroup(main=group.main, aliases=group.aliases, reason=group.reason)
        for group in groups
        if group.aliases
    ]
