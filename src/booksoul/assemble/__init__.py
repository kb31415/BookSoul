"""角色卡组装与校验（阶段 5）+ persona 合并与质量校验（阶段 4）。

按 `PROMPT_DESIGN.md` §11 的交付接口表：

| 产出物 | 路径 |
|---|---|
| Prompt 3 / 4 模板 | `prompts/extract_persona.md`、`prompts/generate_card_fields.md` |
| 合并逻辑（§7） | `assemble/merge.py` |
| 质量校验（§8） | `assemble/validate.py` |

另外：

- `persona.py`：`PersonaIncrement` 等契约（`extract` 与 `merge` 共用，切断循环导入）
- `builder.py`（阶段 5）：补默认值、装配 `character_book`、导出 v2 JSON
"""

from booksoul.assemble.merge import (
    MergedPersona,
    PersonaTimeline,
    TimelinePoint,
    merge_persona_increments,
    relationships_by_target,
)
from booksoul.assemble.persona import (
    PERSONA_TEXT_FIELDS,
    PersonaChange,
    PersonaIncrement,
    Quote,
    RelationshipChange,
)
from booksoul.assemble.validate import (
    CLICHE_BLACKLIST,
    DEFAULT_FIELD_LENGTHS,
    ValidationReport,
    clean_for_matching,
    find_cliches,
    quote_is_grounded,
    validate_persona_increment,
)

__all__ = [
    "CLICHE_BLACKLIST",
    "DEFAULT_FIELD_LENGTHS",
    "MergedPersona",
    "PersonaChange",
    "PersonaIncrement",
    "PersonaTimeline",
    "Quote",
    "RelationshipChange",
    "TimelinePoint",
    "ValidationReport",
    "clean_for_matching",
    "find_cliches",
    "merge_persona_increments",
    "quote_is_grounded",
    "relationships_by_target",
    "validate_persona_increment",
]
