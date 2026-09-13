"""pytest 的最小替身（**仅在真的没装 pytest 时**才会被用到）。

为什么存在：`MVP_PLAN.md` 阶段 1 的验收标准是 `pytest` 全绿，而当前这台机器
既没有 pytest、又无法访问 PyPI（沙箱网络关闭）。为了让验收标准可以真的执行，
`tests/run_tests.py --local` 会把这个替身注册成 `pytest` 模块。

它**只实现测试里真正用到的三个东西**：

- `pytest.fixture`（含 `monkeypatch` / `tmp_path` 两个夹具，见 run_tests.py）
- `pytest.raises`
- `pytest.MonkeyPatch`（类型标注用）

装好 pytest 后：真 pytest 在 `sys.modules` 里已存在，这个替身自动失效，
不需要删任何代码。
"""

from __future__ import annotations

import sys
from typing import Any, Callable, TypeVar

__all__ = ["MonkeyPatch", "fixture", "raises"]

F = TypeVar("F", bound=Callable[..., Any])


class MonkeyPatch:
    """类型占位：真正的实现在 `tests/run_tests.py`。"""

    def setenv(self, name: str, value: str) -> None: ...
    def delenv(self, name: str, raising: bool = True) -> None: ...
    def setattr(self, target: Any, name: str, value: Any) -> None: ...


def fixture(
    func: F | None = None, *, scope: str = "function", autouse: bool = False, **kwargs: Any
) -> Any:
    """标记一个函数为夹具；本替身只认名字，不处理 scope。"""

    def decorate(target: F) -> F:
        setattr(target, "_booksoul_fixture", True)
        return target

    if func is not None:
        return decorate(func)
    return decorate


class _RaisesContext:
    def __init__(self, expected: Any) -> None:
        self.expected = expected
        self.value: BaseException | None = None

    def __enter__(self) -> _RaisesContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is None:
            name = getattr(self.expected, "__name__", str(self.expected))
            raise AssertionError(f"DID NOT RAISE {name}")
        if not issubclass(exc_type, self.expected):
            return False
        self.value = exc
        return True


def raises(expected: Any, *args: Any, **kwargs: Any) -> _RaisesContext:
    """`with pytest.raises(X):` —— 不支持 callable 形式，用不到。"""
    if args or kwargs:
        raise NotImplementedError("替身只支持 with 语句形式")
    return _RaisesContext(expected)


if "pytest" not in sys.modules:  # pragma: no cover - 只在无 pytest 时执行
    sys.modules["pytest"] = sys.modules[__name__]
