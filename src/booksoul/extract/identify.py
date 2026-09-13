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
from booksoul.prompts import load_prompt, render_prompt
from booksoul.storage import ChapterNameCache, RepositorySet

__all__ = [
    "COPULA_TERMS",
    "DEFAULT_CHAPTER_CHAR_LIMIT",
    "DEFAULT_TOP_N",
    "KINSHIP_TERMS",
    "Candidate",
    "CandidateList",
    "ChapterScanResult",
    "CharacterGroup",
    "IdentifyReport",
    "apply_forbidden_merges",
    "build_alias_prompt",
    "build_name_prompt",
    "chapter_cache_fingerprint",
    "collect_contexts",
    "filter_merge_input",
    "find_family_relations",
    "forbidden_merges",
    "identify_candidates",
    "identify_chapters",
    "merge_aliases",
    "normalise_names",
    "parse_alias_groups",
    "parse_names",
    "rank_candidates",
    "resolve_source_form",
    "sample_contexts",
    "scan_chapter",
    "simplify",
]

#: 单章超过这个字数就再切块（`PROMPT_DESIGN.md` §3 建议值）。
DEFAULT_CHAPTER_CHAR_LIMIT: int = 6000

#: 取前 N 作为候选（`PROMPT_DESIGN.md` §3 建议 10–20）。
DEFAULT_TOP_N: int = 15

#: 每个候选名称抽几句原文上下文（§4：2–3 句，上下文是判断的唯一依据）。
CONTEXT_SENTENCES: int = 3


# ══════════════════════════ Prompt（模板文件，照搬设计）══════════════════════════
#
# 模板放在 `prompts/*.md`，不是硬编码在代码里 —— `PROMPT_DESIGN.md` §11 明确要求
# 「落到代码时，Prompt 作为 .md 文件读取（便于随时改，不用改代码）」。
#
# 四个 Prompt 里，本模块用前两个；模板里的 `{chapter_title}` / `{chapter_text}` /
# `{name_contexts}` 由下面两个 builder 填充，其余花括号（JSON 示例、`{{user}}`）
# 原样保留 —— 见 `booksoul.prompts` 的说明。


def build_name_prompt(chapter_title: str, chapter_text: str) -> str:
    """Prompt 1 的完整文本：章节标题 + 正文（§3）。"""
    return render_prompt(
        "identify_names",
        chapter_title=chapter_title or "（无标题）",
        chapter_text=chapter_text,
    )


def build_alias_prompt(name_contexts: str) -> str:
    """Prompt 2 的 user 部分：候选名称及其上下文（§4）。"""
    return render_prompt("merge_aliases", name_contexts=name_contexts)


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
    #: 字形归一（P0-1）发生的改名：`{LLM 的写法: 原文的写法}`。
    renamed: dict[str, str] = field(default_factory=dict)


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
        response = client.complete("", build_name_prompt(chapter.title, chunk), json_mode=True)
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


# ══════════════════════════ 字形归一（P0-1）══════════════════════════
#
# 实测问题：LLM 逐章抽人名时**会在繁简之间摇摆** —— 《宛如约》第 0 章输出简体
# （"司空学士"），第 1–4 章输出繁体（"司空學士"）。后面所有统计都按**字面精确匹配**
# 做，于是同一个人被拆成两个候选：繁体那个统计正常，简体那个 contexts 全空。
#
# 修法：抽完名字后**回原文校正字形** —— 拿每个名字去原文找，优先用原文里真实出现的
# 形态，这样统计、上下文、别名归并全部落在同一个名字上。
# 这是纯后处理，**基于已有缓存即可，不重新调用 LLM**。

#: 繁简单字对照（繁体 → 简体）。只覆盖本项目用得到的字 —— 不引第三方繁简库
#: （`MVP_PLAN.md` §1 依赖最小集），也不用 `str.maketrans`（逐字替换对多对一字
#: 不安全，见 `simplify`）。
_TRADITIONAL_TO_SIMPLIFIED: dict[str, str] = {
    "學": "学", "趙": "赵", "約": "约", "兒": "儿", "個": "个", "這": "这",
    "說": "说", "會": "会", "來": "来", "時": "时", "過": "过", "還": "还",
    "開": "开", "紅": "红", "溫": "温", "習": "习", "古": "古", "媽": "妈",
    "門": "门", "問": "问", "聽": "听", "見": "见", "對": "对", "錢": "钱",
    "風": "风", "雲": "云", "書": "书", "銀": "银", "東": "东", "馬": "马",
    "萬": "万", "與": "与", "為": "为", "無": "无", "雲": "云", "語": "语",
    "誰": "谁", "認": "认", "識": "识", "記": "记", "讓": "让", "請": "请",
    "謝": "谢", "點": "点", "燈": "灯", "燭": "烛", "劍": "剑", "龍": "龙",
    "鳳": "凤", "寶": "宝", "玉": "玉", "將": "将", "軍": "军", "朝": "朝",
}


def simplify(text: str) -> str:
    """按字表把繁体字换成简体（只用于**比对**，不改原文）。"""
    return "".join(_TRADITIONAL_TO_SIMPLIFIED.get(char, char) for char in text)


def display_width(text: str) -> int:
    """粗算显示宽度：汉字按 2、ASCII 按 1（用于"谁的字形更正式"这类比较）。"""
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def resolve_source_form(name: str, chapters: list[Chapter]) -> str:
    """把一个名字**校正成它在原文里真实出现的形态**。

    顺序（每一步都在原文里找精确出现）：

    1. 原文里直接有这个名字 → 原样返回
    2. 否则把它**繁体化**再找（LLM 常给简体，而原文是繁体）
    3. 否则把它的**繁体化形态去比对原文的简体**（原文是简体、LLM 给了繁体）
    4. 都不行 → 返回原名（调用方会发现 contexts 为空，由 P0-2 守卫兜住）
    """
    if not name:
        return name

    for chapter in chapters:
        if name in chapter.content:
            return name

    # ② 简体 → 繁体：把名字里"能繁化"的字换回繁体再找
    reversed_table = {value: key for key, value in _TRADITIONAL_TO_SIMPLIFIED.items()}
    traditional = "".join(reversed_table.get(char, char) for char in name)
    if traditional != name:
        for chapter in chapters:
            if traditional in chapter.content:
                return traditional

    # ③ 原文是简体、LLM 给了繁体
    simplified = simplify(name)
    if simplified != name:
        for chapter in chapters:
            if simplified in chapter.content:
                return simplified

    return name


def normalise_names(
    caches: list[ChapterNameCache], chapters: list[Chapter]
) -> tuple[list[ChapterNameCache], dict[str, str]]:
    """把所有章节缓存里的名字**按原文校正字形**，返回 `(新缓存, 对照表)`。

    对照表是 `{原写法: 原文写法}`，只包含真正发生变化的项（便于人工检查）。
    这是纯后处理：不改调用次数、不重新调 LLM（P0-1 要求走已有缓存）。
    """
    mapping: dict[str, str] = {}
    for cache in caches:
        for chunk in cache.chunks:
            for name in chunk:
                if name not in mapping:
                    mapping[name] = resolve_source_form(name, chapters)

    changed = {source: target for source, target in mapping.items() if source != target}
    if not changed:
        return caches, {}

    normalised: list[ChapterNameCache] = []
    for cache in caches:
        chunks = [[mapping.get(name, name) for name in chunk] for chunk in cache.chunks]
        normalised.append(
            ChapterNameCache.from_chunks(
                chapter_index=cache.chapter_index,
                chunks=chunks,
                fingerprint=cache.fingerprint,
                updated_at=cache.updated_at,
            )
        )
    return normalised, changed



def rank_candidates(
    caches: list[ChapterNameCache],
    chapters: list[Chapter],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> list[Candidate]:
    """按 `PROMPT_DESIGN.md` §3：**出现章数 + 出现次数**排序，取前 N。

    并列时用名字本身做稳定排序（保证同样输入永远同样输出）。
    这里的统计基于**传进来的缓存**；字形归一（P0-1）在调用方先做完。
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


def collect_contexts(
    candidates: list[Candidate], chapters: list[Chapter], limit: int = CONTEXT_SENTENCES
) -> dict[str, list[str]]:
    """给每个候选抽原文上下文，**并按原文校正字形**（P0-1 + §4）。

    为什么这段要单独抽出来：字形校验依赖"原文里有没有这个名字"，
    而 `identify_candidates` 与阶段 8 的 CLI 都需要它 —— 共用一份实现避免两套逻辑。

    返回 `{校正后的名字: [上下文, ...]}`；顺便把校正结果写回 `candidate.name`
    与 `candidate.contexts`（候选表因此显示的是原文里的写法）。
    """
    contexts: dict[str, list[str]] = {}
    for candidate in candidates:
        resolved = resolve_source_form(candidate.name, chapters)
        candidate.name = resolved
        candidate.contexts = sample_contexts(resolved, chapters, limit=limit)
        contexts[resolved] = candidate.contexts
    return contexts


def identify_candidates(
    novel: Novel,
    client: LLMClient,
    repositories: RepositorySet,
    *,
    char_limit: int = DEFAULT_CHAPTER_CHAR_LIMIT,
    top_n: int = DEFAULT_TOP_N,
    force: bool = False,
) -> tuple[CandidateList, IdentifyReport]:
    """阶段 3 主流程：逐章识别 → **字形归一** → 汇总排序 → 落盘 `candidates.json`。"""
    caches, report = identify_chapters(
        novel, client, repositories, char_limit=char_limit, force=force
    )
    # P0-1：回原文校正繁简/异体，让统计与上下文都落在原文真实写法上。
    # 纯后处理 —— 走已有缓存，不额外调用 LLM。
    caches, renamed = normalise_names(caches, novel.chapters)
    report.renamed = renamed

    candidates = rank_candidates(caches, novel.chapters, top_n=top_n)
    # P0-1 复核：TOP_N 是**按归一后的名字**截断的，但上下文仍以原文为准再抽一次，
    # 保证 contexts 里出现的写法与原文一致（也是 P0-2 守卫的判据来源）。
    collect_contexts(candidates, novel.chapters)

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
#
# P0-2 / P0-3 两道**确定性守卫**（`HANDOFF.md` 纪律：确定性的事别交给 LLM 反复烧钱试）：
#
# - P0-2 上下文可用性：`contexts` 为空的候选**不参与归并** —— 没有判断依据就不要判。
# - P0-3 关系句守卫：原文里若有一句话说"A 的儿子是 B"，则**禁止**合并 A/B。
#   实测锚点（《宛如约》）："原來司空學士這個大兒子叫做司空約，表字默愛" ——
#   模型把这句话读成了"司空學士 = 司空約"，于是把儿子并进了父亲名下，
#   导致阶段 4 抽男主时第 0 章整章（父亲 26 次、儿子 0 次）都被当成男主素材。
#
# ⚠️ **不能**用"从不同句共现"当守卫：正常别名（沈知舟 / 沈师兄）本来就常同句出现。

#: 亲属名词（关系句守卫用）。
KINSHIP_TERMS: tuple[str, ...] = (
    "大兒子", "小兒子", "兒子", "大女儿", "小女儿", "女兒", "父親", "母亲", "母親",
    "哥哥", "弟弟", "姐姐", "妹妹", "兄長", "丈夫", "妻子", "娘子", "岳父", "岳母",
    "女婿", "媳婦", "祖父", "祖母", "爺爺", "奶奶", "外祖", "叔父", "伯父", "舅舅",
)
#: 系词 / 命名动词（把两个名字连起来的那类字）。
COPULA_TERMS: tuple[str, ...] = (
    "叫做", "叫作", "名叫", "名喚", "名唤", "喚做", "唤做", "表字", "乃是", "就是",
    "即是", "便是", "名", "字",
)

_FAMILY_PATTERN = re.compile(
    rf"(?P<a>[^\s。！？；，、]{{2,8}})"
    rf"[^\n。！？；]{{0,6}}"
    rf"(?P<kin>{'|'.join(KINSHIP_TERMS)})"
    rf"[^\n。！？；]{{0,6}}?"
    rf"(?P<verb>{'|'.join(COPULA_TERMS)})"
    rf"(?P<b>[^\s。！？；，、]{{2,8}})"
)


def find_family_relations(source_text: str) -> list[tuple[str, str, str]]:
    """在原文里找"亲属关系句"，返回 `[(A, 亲属词, B)]`。

    只认**一句话里同时出现亲属词与系词**的形态，例如：

    - `司空學士這個大兒子叫做司空約` → `(司空學士, 大兒子, 司空約)`
    - `林晚的父親名叫林正` → `(林晚, 父親, 林正)`
    """
    relations: list[tuple[str, str, str]] = []
    for match in _FAMILY_PATTERN.finditer(source_text):
        left, kinship, right = match.group("a"), match.group("kin"), match.group("b")
        if left == right:
            continue
        relations.append((left, kinship, right))
    return relations


def forbidden_merges(
    names: Iterable[str],
    chapters: list[Chapter],
    *,
    max_pairs: int = 200,
) -> dict[frozenset[str], str]:
    """算出**禁止合并**的名字对（P0-3）。

    判据：原文里存在一句话，把一个名字与另一个名字用**亲属词 + 系词**连起来。
    返回 `{frozenset({A, B}): 证据}`。

    只在关系句里出现过的名字上验证，不做笛卡尔积全扫；`max_pairs` 兜住极端情况。
    """
    candidate_names = [name for name in names if name]
    if len(candidate_names) < 2 or max_pairs <= 0:
        return {}

    full_text = "\n".join(chapter.content for chapter in chapters)
    forbidden: dict[frozenset[str], str] = {}
    checked = 0

    for left, kinship, right in find_family_relations(full_text):
        hits_left = [name for name in candidate_names if name in left]
        hits_right = [name for name in candidate_names if name in right]
        for name_a in hits_left:
            for name_b in hits_right:
                if name_a == name_b:
                    continue
                checked += 1
                if checked > max_pairs:
                    return forbidden
                forbidden[frozenset({name_a, name_b})] = f"{left}…{kinship}…{right}"
    return forbidden


def filter_merge_input(
    name_contexts: dict[str, list[str]],
) -> tuple[dict[str, list[str]], list[str]]:
    """P0-2：剔除 `contexts` 为空的候选，返回 `(可归并的上下文, 被剔除的名字)`。

    设计依据（§4）："上下文是判断的唯一依据 —— 没有上下文，LLM 只能靠猜"。
    所以没上下文的候选**连送都不送**。
    """
    kept: dict[str, list[str]] = {}
    dropped: list[str] = []
    for name, contexts in name_contexts.items():
        if contexts:
            kept[name] = contexts
        else:
            dropped.append(name)
    return kept, dropped


def apply_forbidden_merges(
    groups: list[CharacterGroup],
    forbidden: dict[frozenset[str], str],
) -> tuple[list[CharacterGroup], list[str]]:
    """P0-3：把 LLM 归并结果里**违反关系句守卫**的别名摘出来。

    返回 `(过滤后的组, 被摘掉的说明)`。若一组的主名被守卫保护，
    只摘掉违规的别名；组内其余别名保留。
    """
    if not forbidden:
        return groups, []

    filtered: list[CharacterGroup] = []
    notes: list[str] = []
    for group in groups:
        if not group.aliases:
            continue
        kept_aliases: list[str] = []
        for alias in group.aliases:
            evidence = forbidden.get(frozenset({group.main, alias}))
            if evidence:
                notes.append(f"拒绝合并 {group.main} ← {alias}（原文为亲属关系：{evidence}）")
                continue
            kept_aliases.append(alias)
        if kept_aliases:
            filtered.append(group.model_copy(update={"aliases": kept_aliases}))
    return filtered, notes


def merge_aliases(
    client: LLMClient,
    name_contexts: dict[str, list[str]],
    *,
    guard_contexts: bool = True,
    forbidden: dict[frozenset[str], str] | None = None,
) -> tuple[list[CharacterGroup], list[str]]:
    """用 Prompt 2 判断哪些名字指同一个人物（§4）。

    `name_contexts` 的格式：`{名字: [含该名字的原文句子, ...]}`。

    返回 `(人物组, 守卫说明)`。默认开启两道守卫：

    - `guard_contexts=True`：`contexts` 为空的候选不参与归并（P0-2）
    - `forbidden`：关系句算出的禁合并对（P0-3）
    """
    notes: list[str] = []
    if not name_contexts:
        return [], notes

    candidates = name_contexts
    if guard_contexts:
        candidates, dropped = filter_merge_input(name_contexts)
        notes.extend(f"跳过无上下文的候选：{name}" for name in dropped)
        if len(candidates) < 2:
            return [], notes

    lines: list[str] = []
    for name, contexts in candidates.items():
        marks = "".join(f"{chr(0x2460 + index)}{quote}" for index, quote in enumerate(contexts[:3]))
        lines.append(f"- {name}：{marks or '（无上下文）'}")

    response = client.complete("", build_alias_prompt("\n".join(lines)), json_mode=True)
    try:
        groups = parse_alias_groups(response.json())
    except (ValueError, TypeError):
        return [], notes

    # 只保留真正发生了合并的组（单名成组对下游没意义）
    merged = [
        CharacterGroup(main=group.main, aliases=group.aliases, reason=group.reason)
        for group in groups
        if group.aliases
    ]

    if forbidden:
        merged, rejected = apply_forbidden_merges(merged, forbidden)
        notes.extend(rejected)
    return merged, notes
