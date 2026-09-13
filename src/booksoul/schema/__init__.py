"""数据契约层：内部 schema 与酒馆（Tavern）v2/v3 导入导出。

- `character.py`：`CharacterCard` / `LoreEntry` / `TimelineEvent` / `Relation` / `PlotNode`
- `tavern.py`：`to_tavern_v2()` / `from_tavern()`
"""

from booksoul.schema.character import (
    CharacterCard,
    LoreEntry,
    PlotNode,
    Relation,
    TimelineEvent,
)
from booksoul.schema.tavern import (
    BOOKSOUL_EXTENSION_KEY,
    SPEC_V2,
    SPEC_V2_VERSION,
    SPEC_V3,
    SPEC_V3_VERSION,
    from_tavern,
    to_tavern_v2,
)

__all__ = [
    "BOOKSOUL_EXTENSION_KEY",
    "SPEC_V2",
    "SPEC_V2_VERSION",
    "SPEC_V3",
    "SPEC_V3_VERSION",
    "CharacterCard",
    "LoreEntry",
    "PlotNode",
    "Relation",
    "TimelineEvent",
    "from_tavern",
    "to_tavern_v2",
]
