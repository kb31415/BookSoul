"""存储层（`MVP_PLAN.md` §1「迭代友好性约束」+ `HANDOFF.md` §2 结构红线）。

- `models.py`：`NovelRecord` / `MemoryEntry` / `SessionLog` 数据契约
- `repository.py`：`NovelRepository` / `CardRepository` / `MemoryRepository` /
  `SessionRepository` 四个接口 + `FileXxxRepository` 文件实现 + `build_repositories()`

**业务代码只依赖接口**，不允许直接 `open("data/...json")`
（那是结构红线 ①，违反它第二阶段迁 Postgres 时要重写）。
"""

from booksoul.storage.models import (
    MEMORY_SOURCES,
    MEMORY_TIERS,
    MemoryEntry,
    NovelRecord,
    SessionLog,
    utc_now_iso,
)
from booksoul.storage.repository import (
    CardRepository,
    FileCardRepository,
    FileMemoryRepository,
    FileNovelRepository,
    FileSessionRepository,
    MemoryRepository,
    NovelRepository,
    RepositorySet,
    SessionRepository,
    build_repositories,
    content_id,
)

__all__ = [
    "MEMORY_SOURCES",
    "MEMORY_TIERS",
    "CardRepository",
    "FileCardRepository",
    "FileMemoryRepository",
    "FileNovelRepository",
    "FileSessionRepository",
    "MemoryEntry",
    "MemoryRepository",
    "NovelRecord",
    "NovelRepository",
    "RepositorySet",
    "SessionLog",
    "SessionRepository",
    "build_repositories",
    "content_id",
    "utc_now_iso",
]
