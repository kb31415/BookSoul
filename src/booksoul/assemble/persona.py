"""Persona 增量契约（`PROMPT_DESIGN.md` §5 的输出格式）。

**为什么单独一个模块**：`extract/extract.py`（跑 Prompt 3）和 `assemble/merge.py`
（按 §7 合并）都要用它，但 `assemble/__init__.py` 又要导出 `extract` 用的东西 ——
把模型放这里就切断了循环导入，职责也更清楚。

`speech_style` 与 `speech_samples` 的区别（容易混，写清楚）：

- `speech_style`：**一条结论**（"说话短促，习惯用反问"），进画像文本字段
- `speech_samples`：**可直接当 few-shot 的原话素材**，§7 要求全部保留、由 Prompt 4 挑选
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PERSONA_TEXT_FIELDS",
    "PersonaChange",
    "PersonaIncrement",
    "Quote",
    "RelationshipChange",
]
#: 由 LLM 逐章填写的文本字段（不含关系 / 引用 / 样例）。
#:
#: 放在**模块级**而不是 `PersonaIncrement` 类体里：pydantic 会把类体里带类型注解的
#: 名字当成模型字段，`tuple[str, ...]` 会被解析成字段定义而不是常量（踩过）。
PERSONA_TEXT_FIELDS: tuple[str, ...] = (
    "personality",
    "desire",
    "flaw",
    "secret",
    "speech_style",
)


class Quote(BaseModel):
    """一条原文引用依据（§5 质量约束 1：每条结论必须附原文引用）。"""

    model_config = ConfigDict(extra="ignore")

    field: str = ""
    text: str = ""
    #: explicit = 文中明说；inferred = 模型推断（§5 质量约束 3）。
    confidence: str = "explicit"


class RelationshipChange(BaseModel):
    """本章中与某个 target 的关系变化（§5 字段定义）。"""

    model_config = ConfigDict(extra="ignore")

    target: str = ""
    change: str = ""


class PersonaChange(BaseModel):
    """与已有画像的冲突（§5 质量约束 5：冲突标记而非静默覆盖）。

    `from_` 在 JSON 里的键是 `from`（`from` 是 Python 关键字），与
    `schema.character.Relation` 同一套处理方式。
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    field: str = ""
    from_: str = Field(default="", alias="from")
    to: str = ""
    reason: str = ""


class PersonaIncrement(BaseModel):
    """一章的 persona 增量 —— 严格对应 `PROMPT_DESIGN.md` §5 的输出格式。"""

    model_config = ConfigDict(extra="ignore")

    personality: str = ""
    desire: str = ""
    flaw: str = ""
    secret: str = ""
    speech_style: str = ""
    relationships: list[RelationshipChange] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    speech_samples: list[str] = Field(default_factory=list)
    changes: list[PersonaChange] = Field(default_factory=list)

    # ── 便捷视图 ──

    def text_values(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in PERSONA_TEXT_FIELDS}

    def is_empty(self) -> bool:
        """这一章对画像有没有贡献？

        判据 = **五个文本字段全空 且 relationships 为空**。

        刻意**不**把 `quotes` / `speech_samples` 算进来：它们是"证据/素材"，
        不是"结论"。一个只有引文、给不出成句性格的增量不该被当成抽到了人设
        （但它也不会因此丢失 —— §7 要求这两者"全部保留"，合并时会照收）。
        """
        return not any(self.text_values().values()) and not self.relationships

    def non_empty_fields(self) -> list[str]:
        """有哪些文本字段是非空的（§8 的「引用完整性」检查要用）。"""
        return [name for name, value in self.text_values().items() if value.strip()]
