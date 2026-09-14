"""角色卡数据契约（`PROJECT_DESIGN.md` §6.4）。

设计要点：

1. **标准字段**（`name` / `description` / ... ）与酒馆 v2 规范同名同义，直出。
2. **立体扩展字段**（`desire` / `flaw` / `secret` / `timeline` / `relations` / `plot_nodes`）
   是本项目特有信息，导出时统一收进 `data.extensions.booksoul`（见 `tavern.py`）。
3. **所有非必填字段都给默认值** —— 导入外部卡（缺字段、无 extensions）时不报错。
4. `relations` 单向存储：每条关系记为 `from → to`，方向性本身就是信息（§6.3）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CharacterCard",
    "LoreEntry",
    "PlotNode",
    "Relation",
    "TimelineEvent",
]


class LoreEntry(BaseModel):
    """世界书条目：命中 `keys` 时把 `content` 注入上下文。"""

    model_config = ConfigDict(extra="ignore")

    keys: list[str] = Field(default_factory=list)
    content: str = ""
    enabled: bool = True
    insertion_order: int = 0
    case_sensitive: bool = False


class TimelineEvent(BaseModel):
    """时间线事件：先天记忆的载体（§13.1 canon 层）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    order: int = 0
    summary: str = ""
    actors: list[str] = Field(default_factory=list)
    chapter: str | None = None
    quote: str | None = None


class Relation(BaseModel):
    """单向关系：`from_ → to`。

    内部字段名 `from_`（`from` 是 Python 关键字），JSON 里的键是 `from`。
    两种写法都能构造：`Relation(**{"from": "A", "to": "B"})` 与
    `Relation(from_="A", to="B")`。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    from_: str = Field(default="", alias="from")
    to: str = ""
    kind: str = ""
    description: str = ""
    evolution: list[str] = Field(default_factory=list)


class PlotNode(BaseModel):
    """剧情节点。**MVP 阶段留空**，属第二阶段。"""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    order: int = 0
    title: str = ""
    description: str = ""
    chapter: str | None = None
    branch: str = "canon"


class CharacterCard(BaseModel):
    """角色卡：角色 = 数据而非代码。

    这是整个项目改动成本最高的契约，因此字段一次到位（§6.1）。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # ── 标准字段（酒馆 v2/v3 兼容）──
    name: str
    description: str
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    character_book: list[LoreEntry] = Field(default_factory=list)
    creator_notes: str = ""
    tags: list[str] = Field(default_factory=list)
    character_version: str = "1.0"

    # ── 立体扩展（本项目特有）──
    desire: str = ""
    flaw: str = ""
    secret: str = ""
    #: 说话风格。`PROMPT_DESIGN.md` §5 的 Prompt 3 逐章抽它、§6 的 `mes_example`
    #: 质量依赖它，但 §6.1 的字段表没列 —— 属实现期补的契约，与
    #: `desire` / `flaw` / `secret` 同层，走 `extensions.booksoul`。
    speech_style: str = ""
    timeline: list[TimelineEvent] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    plot_nodes: list[PlotNode] = Field(default_factory=list)

    # ── 元数据 ──
    source_book: str | None = None
    extraction_meta: dict[str, Any] = Field(default_factory=dict)

    # ── 便捷视图 ──

    @property
    def has_extraction_evidence(self) -> bool:
        """是否带有原文引用依据（§6.1 `extraction_meta.quotes`）。

        阶段 5 的校验用：无依据的字段宁可留空，但一旦有值就应能溯源。
        """
        quotes = self.extraction_meta.get("quotes")
        return isinstance(quotes, dict) and bool(quotes)

    # ── 酒馆格式互转（实现在 tavern.py，避免循环依赖）──

    def to_tavern_v2(self) -> dict[str, Any]:
        """导出为酒馆 v2 角色卡（`PROJECT_DESIGN.md` §6.2）。"""
        from booksoul.schema.tavern import to_tavern_v2

        return to_tavern_v2(self)

    @classmethod
    def from_tavern(cls, raw: dict[str, Any]) -> CharacterCard:
        """从酒馆 v2/v3 卡导入，兼容有 spec 包装 / 裸字段 / 缺 extensions。"""
        from booksoul.schema.tavern import from_tavern

        return from_tavern(raw)
