"""角色抽取层（阶段 3–4）。

- `identify.py`（阶段 3）：逐章粗扫人名 → 汇总词频 → 候选列表 + 别名归并
- `extract.py`（阶段 4，🔴 风险点 1）：按相关段落精抽立体字段 + 原文引用依据
"""

from booksoul.extract.identify import (
    ALIAS_SYSTEM_PROMPT,
    DEFAULT_CHAPTER_CHAR_LIMIT,
    DEFAULT_TOP_N,
    NAME_SYSTEM_PROMPT,
    Candidate,
    CandidateList,
    CharacterGroup,
    ChapterScanResult,
    IdentifyReport,
    build_alias_prompt,
    build_name_prompt,
    chapter_cache_fingerprint,
    identify_candidates,
    identify_chapters,
    merge_aliases,
    parse_alias_groups,
    parse_names,
    rank_candidates,
    sample_contexts,
    scan_chapter,
)

__all__ = [
    "ALIAS_SYSTEM_PROMPT",
    "DEFAULT_CHAPTER_CHAR_LIMIT",
    "DEFAULT_TOP_N",
    "NAME_SYSTEM_PROMPT",
    "Candidate",
    "CandidateList",
    "CharacterGroup",
    "ChapterScanResult",
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
