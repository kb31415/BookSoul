"""LLM 调用层（阶段 3）。

`client.py`：LiteLLM 封装（重试 / 超时 / usage 记录）。

**全局约定**：所有 LLM 调用统一走这里，业务代码里不允许直接调 SDK
（便于换模型、加成本统计，见 `MVP_PLAN.md` §1）。
"""

from booksoul.llm.client import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    LLMCallError,
    LLMClient,
    LLMResponse,
    LLMUsage,
)

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "LLMCallError",
    "LLMClient",
    "LLMResponse",
    "LLMUsage",
]
