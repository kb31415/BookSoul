"""存储层的数据契约（`MVP_PLAN.md` §1「迭代友好性约束」+ `HANDOFF.md` §2 结构红线）。

这里只放**结构**，不放实现：

- `NovelRecord`：阶段 2 的章节解析产物（`data/novels/{book_id}.json`）
- `MemoryEntry`：记忆条目（`PROJECT_DESIGN.md` §13.6，**绝不能退化成裸 messages 数组**）
- `SessionLog`：会话（`data/sessions/{session_id}.jsonl`）+ 预留的 `state`

> 结构红线的原则：**MVP 可以砍「实现」，但不能砍「数据契约」和「模块边界」。**
> 所以这些模型里的字段即使 MVP 用不上（`tier` 全填 heuristic、`state` 为空），
> 也必须留在契约里 —— 后面不是"加功能"，而是"重写"。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MEMORY_TIERS",
    "MEMORY_SOURCES",
    "MemoryEntry",
    "NovelRecord",
    "SessionLog",
    "utc_now_iso",
]

#: 记忆层级（设计 §13.1：canon / heuristic / reflex）。MVP 只写 heuristic，不做晋升。
MemoryTier = Literal["canon", "heuristic", "reflex"]
MEMORY_TIERS: tuple[str, ...] = ("canon", "heuristic", "reflex")

#: 记忆来源（设计 §13.6）：`book` = 先天（离线抽取），`dialogue` = 后天（对话产生）。
MemorySource = Literal["book", "dialogue"]
MEMORY_SOURCES: tuple[str, ...] = ("book", "dialogue")


def utc_now_iso() -> str:
    """带时区的 ISO 时间戳（落盘用，便于人工读）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class NovelRecord(BaseModel):
    """一本小说的解析产物。

    等价于 `booksoul.ingest.parser.Novel`，这里再包一层是**刻意**的：
    存储层不应该依赖解析层的模型（否则换实现时两层绑死）。
    `payload` 原样保存解析结果，存储层不解释它的内部结构。
    """

    model_config = ConfigDict(extra="ignore")

    book_id: str
    source_path: str | None = None
    encoding: str = "utf-8"
    char_count: int = 0
    chapter_count: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class MemoryEntry(BaseModel):
    """一条记忆（`PROJECT_DESIGN.md` §13.6 的存储 schema，逐字段对齐）。

    **这是结构红线明确点名的一项**：记忆必须存成条目，不能存成裸 messages 数组，
    否则第二阶段上三层契约（canon / heuristic / reflex）时整个记忆系统要重写。
    """

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    character_id: str = ""
    #: 层级。MVP 全填 `heuristic`，不做晋升（晋升契约属第二阶段）。
    tier: MemoryTier = "heuristic"
    #: 来源：`book`（先天，离线抽取）｜`dialogue`（后天，对话产生）。
    source: MemorySource = "dialogue"
    content: str = ""
    #: 原文引用依据（先天记忆用来溯源，设计 §6.1 的 quotes）。
    quote: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    #: 被检索注入的次数（后续做晋升/淘汰要用，MVP 只累加）。
    hits: int = 0
    #: 是否被人工修正过（设计 §13.5 的冲突消解）。
    corrected: bool = False
    #: 与之冲突的记忆 id 列表（MVP 留空）。
    conflicts_with: list[str] = Field(default_factory=list)

    def touch(self) -> MemoryEntry:
        """命中一次：`hits += 1`（返回新对象，不改自身）。"""
        return self.model_copy(update={"hits": self.hits + 1})


class SessionLog(BaseModel):
    """一个会话（`data/sessions/{session_id}.jsonl`）。

    `state` 是**结构红线点名的预留字段**（MVP 可为空）：后续 mood（§14.4）、
    剧情推进、关系状态机都往这里放，不需要改会话结构。
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    character_id: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    #: 预留：mood / 剧情 / 关系状态（MVP 为空 dict）。
    state: dict[str, Any] = Field(default_factory=dict)
    #: 会话消息。MVP 用 dict 保持灵活（对话消息的确切结构属阶段 6 设计范围）。
    messages: list[dict[str, Any]] = Field(default_factory=list)
