"""LiteLLM 封装：重试 / 超时 / usage 记录（`MVP_PLAN.md` 阶段 3 任务 1）。

**全局约定**（`MVP_PLAN.md` §1）：所有 LLM 调用统一走这里，
业务代码里不允许直接调 SDK —— 便于换模型、加成本统计。

设计要点：

1. **可注入**：`LLMClient` 接受一个 `completion_fn`。生产用 `litellm.completion`，
   测试塞假函数 —— 业务逻辑与网络解耦，单测不花钱。
2. **重试一次不够就报错**：`MVP_PLAN.md` 全局约定是「Pydantic 校验 + 失败重试一次，
   仍失败则报错退出（不静默吞掉）」。这里负责**调用层**的重试，校验层的重试在调用方。
3. **usage 累计**：每次调用的 token 用量累加进 `LLMUsage`，
   阶段 10 的成本量化直接读它，不用再改代码。
4. **JSON 模式**：`response_format={"type": "json_object"}` + 调用方自己 Pydantic 校验。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from booksoul.config import Settings, load_settings, require_api_key

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "LLMCallError",
    "LLMClient",
    "LLMResponse",
    "LLMUsage",
]

#: 单次请求超时（秒）。
DEFAULT_TIMEOUT: float = 120.0

#: 失败重试次数（不含首次）。阶段 3 是"量大任务简单"，多试一次很便宜。
DEFAULT_MAX_RETRIES: int = 2

#: 抽取任务要的是稳定复现，不是创造力 —— 一律 0。
DEFAULT_TEMPERATURE: float = 0.0


class LLMCallError(RuntimeError):
    """LLM 调用在重试后仍然失败。

    **必须让上层看到** —— `MVP_PLAN.md` 全局约定不允许静默吞掉失败
    （否则会产出"看起来成功、其实缺了一大半"的中间结果）。
    """


# ────────────────────────── usage 统计 ──────────────────────────


@dataclass
class LLMUsage:
    """token 用量累计器（阶段 10 成本量化的数据来源）。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    failed_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, response: "LLMResponse | None", *, failed: bool = False) -> None:
        self.calls += 1
        if failed:
            self.failed_calls += 1
            return
        if response is None:
            return
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens
        self.cached_tokens += response.cached_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
        }


# ────────────────────────── 响应 ──────────────────────────


@dataclass
class LLMResponse:
    """一次调用的结果（只保留业务需要的字段）。"""

    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    raw: Any = None

    def json(self) -> Any:
        """把 `content` 解析成 JSON。

        LLM 偶尔会用 ```json 包裹（即使说了"只输出 JSON"），这里做一次剥离。
        """
        text = self.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1] if "\n" in text else text
            if text.endswith("```"):
                text = text[: -3]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)


def _extract_cached_tokens(usage: Any) -> int:
    """从 litellm 的 usage 里取"缓存命中"的 prompt token 数（字段名不稳定，多路兜底）。"""
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    for source in (details, usage):
        if source is None:
            continue
        for name in ("cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens"):
            value = getattr(source, name, None)
            if isinstance(value, int):
                return value
    return 0


def _default_completion_fn(**kwargs: Any) -> Any:
    """生产实现：走 litellm（延迟 import —— 没装依赖时不该影响其他阶段）。"""
    import litellm  # noqa: PLC0415

    return litellm.completion(**kwargs)


# ────────────────────────── 客户端 ──────────────────────────


@dataclass
class LLMClient:
    """LLM 调用的唯一入口。

    用法：

    ```python
    client = LLMClient.from_settings()          # 读环境变量拿 Key 与模型
    response = client.complete("你是……", "章节文本……", json_mode=True)
    print(client.usage.as_dict())               # token 用了多少
    ```
    """

    api_key: str
    model: str
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    temperature: float = DEFAULT_TEMPERATURE
    completion_fn: Callable[..., Any] = field(default=_default_completion_fn, repr=False)
    sleep_fn: Callable[[float], None] = field(default=time.sleep, repr=False)
    usage: LLMUsage = field(default_factory=LLMUsage)

    # ── 构造 ──

    @classmethod
    def from_settings(cls, settings: Settings | None = None, **overrides: Any) -> "LLMClient":
        """从配置构造；Key 缺失时**立刻报可操作的错**（而不是等到调用时才炸）。"""
        current = settings or load_settings()
        return cls(
            api_key=require_api_key(current),
            model=current.model_name,
            **overrides,
        )

    # ── 调用 ──

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """一次对话式补全（system + user），失败按 `max_retries` 退避重试。

        重试耗尽后抛 `LLMCallError` —— 不返回空串、不静默吞掉。
        """
        attempts = self.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = self._call_once(
                    system=system,
                    user=user,
                    json_mode=json_mode,
                    temperature=self.temperature if temperature is None else temperature,
                    model=model or self.model,
                )
            except Exception as exc:  # noqa: BLE001 - 统一转成 LLMCallError 抛出
                last_error = exc
                self.usage.add(None, failed=True)
                if attempt + 1 < attempts:
                    self.sleep_fn(2.0**attempt)  # 1s, 2s, ...
                continue

            self.usage.add(response)
            return response

        raise LLMCallError(
            f"LLM 调用重试 {attempts} 次仍失败（model={model or self.model}）："
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    def _call_once(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool,
        temperature: float,
        model: str,
    ) -> LLMResponse:
        messages: Sequence[dict[str, str]] = (
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": self.api_key,
            "messages": list(messages),
            "temperature": temperature,
            "timeout": self.timeout,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        raw = self.completion_fn(**kwargs)
        usage = getattr(raw, "usage", None)
        message = raw.choices[0].message

        return LLMResponse(
            content=getattr(message, "content", "") or "",
            model=str(getattr(raw, "model", "") or model),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cached_tokens=_extract_cached_tokens(usage),
            raw=raw,
        )

    # ── 自检 ──

    def ping(self) -> str:
        """最小真实调用：确认 Key / 模型 / 网络都通（返回模型自报的名字）。"""
        response = self.complete(
            "你是一个测试助手。",
            "只回复两个字：可用",
            temperature=0.0,
        )
        return response.model
