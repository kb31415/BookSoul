"""LLM 客户端测试（`MVP_PLAN.md` 阶段 3 任务 1：重试 / 超时 / usage 记录）。

全部用假 `completion_fn`，**不产生任何真实调用**。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from booksoul.llm import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TEMPERATURE,
    LLMCallError,
    LLMClient,
    LLMResponse,
    LLMUsage,
)
from booksoul.config import Settings


# ────────────────────────── 假响应 ──────────────────────────


def fake_response(
    content: str = '{"characters": ["沈知舟"]}',
    *,
    model: str = "deepseek-flash",
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
    cached_tokens: int = 0,
) -> Any:
    """构造一个形如 litellm `ModelResponse` 的对象。"""
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        ),
    )


def make_client(**overrides: Any) -> LLMClient:
    """默认带上「不真的 sleep」的假客户端；overrides 优先。"""
    defaults: dict[str, Any] = {
        "api_key": "sk-fake",
        "model": "deepseek/deepseek-chat",
        "sleep_fn": lambda _: None,
    }
    defaults.update(overrides)
    return LLMClient(**defaults)


# ────────────────────────── 正常调用 ──────────────────────────


def test_complete_returns_content_and_usage() -> None:
    client = make_client(completion_fn=lambda **_: fake_response("你好"))

    response = client.complete("系统", "用户")

    assert response.content == "你好"
    assert response.model == "deepseek-flash"
    assert response.prompt_tokens == 100
    assert response.completion_tokens == 20


def test_usage_accumulates_across_calls() -> None:
    client = make_client(completion_fn=lambda **_: fake_response())

    client.complete("s", "u")
    client.complete("s", "u")
    client.complete("s", "u")

    assert client.usage.calls == 3
    assert client.usage.prompt_tokens == 300
    assert client.usage.total_tokens == 360
    assert client.usage.failed_calls == 0


def test_usage_as_dict_shape() -> None:
    client = make_client(completion_fn=lambda **_: fake_response())
    client.complete("s", "u")

    payload = client.usage.as_dict()

    assert payload["calls"] == 1
    assert payload["prompt_tokens"] == 100
    assert payload["total_tokens"] == 120


def test_cached_tokens_are_recorded() -> None:
    """阶段 6 的 cache 工程要看命中量，这里必须真的记下来。"""
    client = make_client(completion_fn=lambda **_: fake_response(cached_tokens=64))

    response = client.complete("s", "u")

    assert response.cached_tokens == 64
    assert client.usage.cached_tokens == 64


def test_cached_tokens_field_fallback() -> None:
    """字段名不稳定时走兜底（prompt_cache_hit_tokens）。"""
    raw = fake_response()
    raw.usage.prompt_tokens_details = None
    raw.usage.prompt_cache_hit_tokens = 32
    client = make_client(completion_fn=lambda **_: raw)

    assert client.complete("s", "u").cached_tokens == 32


def test_default_temperature_is_zero() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response()

    make_client(completion_fn=fake).complete("s", "u")

    assert captured["temperature"] == DEFAULT_TEMPERATURE == 0.0


def test_json_mode_sets_response_format() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response()

    make_client(completion_fn=fake).complete("s", "u", json_mode=True)

    assert captured["response_format"] == {"type": "json_object"}


def test_json_mode_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response()

    make_client(completion_fn=fake).complete("s", "u")

    assert "response_format" not in captured


def test_messages_carry_system_and_user() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response()

    make_client(completion_fn=fake).complete("系统提示", "用户输入")

    assert captured["messages"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "用户输入"},
    ]


def test_timeout_and_model_are_passed_through() -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_response()

    make_client(timeout=9.5, completion_fn=fake).complete("s", "u", model="别的模型")

    assert captured["timeout"] == 9.5
    assert captured["model"] == "别的模型"
    assert captured["api_key"] == "sk-fake"


# ────────────────────────── 重试 ──────────────────────────


def test_retries_then_succeeds() -> None:
    attempts = {"count": 0}

    def flaky(**_: Any) -> Any:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("网络抖动")
        return fake_response("终于成功")

    slept: list[float] = []
    client = make_client(completion_fn=flaky, sleep_fn=slept.append, max_retries=3)

    response = client.complete("s", "u")

    assert response.content == "终于成功"
    assert attempts["count"] == 3
    assert slept == [1.0, 2.0]  # 指数退避
    assert client.usage.failed_calls == 2
    assert client.usage.calls == 3


def test_raises_after_exhausting_retries() -> None:
    """`MVP_PLAN.md` 全局约定：仍失败则报错退出，不静默吞掉。"""
    def always_fail(**_: Any) -> Any:
        raise RuntimeError("一直失败")

    client = make_client(completion_fn=always_fail, sleep_fn=lambda _: None, max_retries=2)

    with pytest.raises(LLMCallError) as excinfo:
        client.complete("s", "u")

    message = str(excinfo.value)
    assert "重试 3 次仍失败" in message
    assert "一直失败" in message
    assert client.usage.failed_calls == 3
    assert client.usage.prompt_tokens == 0


def test_no_retry_by_default_count() -> None:
    """默认重试次数 = DEFAULT_MAX_RETRIES（不含首次）。"""
    calls = {"count": 0}

    def fail(**_: Any) -> Any:
        calls["count"] += 1
        raise RuntimeError("x")

    client = make_client(completion_fn=fail, sleep_fn=lambda _: None)

    with pytest.raises(LLMCallError):
        client.complete("s", "u")

    assert calls["count"] == DEFAULT_MAX_RETRIES + 1


def test_original_exception_is_chained() -> None:
    def fail(**_: Any) -> Any:
        raise ValueError("底层原因")

    client = make_client(completion_fn=fail, sleep_fn=lambda _: None, max_retries=0)

    with pytest.raises(LLMCallError) as excinfo:
        client.complete("s", "u")

    assert isinstance(excinfo.value.__cause__, ValueError)


# ────────────────────────── 构造与自检 ──────────────────────────


def test_from_settings_requires_api_key() -> None:
    from booksoul.config import ENV_API_KEY

    with pytest.raises(RuntimeError) as excinfo:
        LLMClient.from_settings(Settings(api_key=""), )

    assert ENV_API_KEY in str(excinfo.value)


def test_from_settings_uses_configured_model() -> None:
    client = LLMClient.from_settings(
        Settings(api_key="sk-x", model_name="deepseek/deepseek-reasoner"),
        completion_fn=lambda **_: fake_response(),
    )

    assert client.model == "deepseek/deepseek-reasoner"
    assert client.api_key == "sk-x"


def test_ping_returns_reported_model() -> None:
    client = make_client(completion_fn=lambda **_: fake_response("可用"))

    assert client.ping() == "deepseek-flash"


# ────────────────────────── JSON 解析 ──────────────────────────


def test_response_json_parses_plain_object() -> None:
    response = LLMResponse(content='{"characters": ["甲"]}')

    assert response.json() == {"characters": ["甲"]}


def test_response_json_strips_code_fence() -> None:
    """LLM 常把 JSON 包进 ```json，即使提示里说了"只输出 JSON"。"""
    response = LLMResponse(content='```json\n{"characters": ["甲"]}\n```')

    assert response.json() == {"characters": ["甲"]}


def test_response_json_strips_bare_fence() -> None:
    response = LLMResponse(content='```\n{"characters": ["甲"]}\n```')

    assert response.json() == {"characters": ["甲"]}


def test_response_json_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        LLMResponse(content="这不是 JSON").json()


def test_usage_add_ignores_none_response() -> None:
    usage = LLMUsage()

    usage.add(None, failed=True)

    assert usage.calls == 1
    assert usage.failed_calls == 1
    assert usage.total_tokens == 0
