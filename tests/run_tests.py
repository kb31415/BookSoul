"""测试入口：有 pytest 就用 pytest，没有就用内置的极简运行器。

为什么需要这个：`MVP_PLAN.md` 阶段 1 的验收标准是 `pytest` 全绿。本机
（Python 3.14.3）当前**没有装 pytest 且无法访问 PyPI**（沙箱内网络关闭），
为了仍然能真实验收，这里提供一个只依赖标准库的兜底运行器。

用法：

    python tests/run_tests.py          # 自动选择：pytest 优先
    python tests/run_tests.py --local  # 强制用内置运行器

安装依赖后（`pip install -e ".[dev]"`）请直接用 `pytest`：

    pytest
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import itertools
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SRC_DIR = REPO_ROOT / "src"

#: 沙箱下系统临时目录可能不可写，统一用仓库内 `.tmp`。
TMP_ROOT = REPO_ROOT / ".tmp"


# ────────────────────────── 夹具替身 ──────────────────────────


class MonkeyPatch:
    """`monkeypatch` 夹具的最小实现：setenv / delenv。"""

    def __init__(self) -> None:
        self._env_backup: dict[str, str | None] = {}
        self._attr_backup: list[tuple[Any, str, Any]] = []

    # 环境变量
    def setenv(self, name: str, value: str) -> None:
        self._env_backup.setdefault(name, os.environ.get(name))
        os.environ[name] = str(value)

    def delenv(self, name: str, raising: bool = True) -> None:
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        self._env_backup.setdefault(name, os.environ.get(name))
        del os.environ[name]

    # 属性
    def setattr(self, target: Any, name: str, value: Any) -> None:
        self._attr_backup.append((target, name, getattr(target, name, None)))
        setattr(target, name, value)

    def undo(self) -> None:
        for name, old in self._env_backup.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        self._env_backup.clear()
        for target, name, old in reversed(self._attr_backup):
            setattr(target, name, old)
        self._attr_backup.clear()


class _RaisesContext:
    """`pytest.raises` 的最小实现（只需要 `with ... :` 与 `.value`）。"""

    def __init__(self, expected: type[BaseException] | tuple[type[BaseException], ...]) -> None:
        self.expected = expected
        self.value: BaseException | None = None

    def __enter__(self) -> _RaisesContext:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            names = getattr(self.expected, "__name__", str(self.expected))
            raise AssertionError(f"DID NOT RAISE {names}")
        if not issubclass(exc_type, self.expected):
            return False  # 让真正的异常冒出来
        self.value = exc
        return True


# ────────────────────────── 收集与执行 ──────────────────────────


def _import_module(path: Path) -> Any:
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    return importlib.import_module(path.stem)


def _collect_fixtures(modules: Iterable[Any]) -> dict[str, Callable[..., Any]]:
    """收集夹具：既认真 pytest 的标记，也认本仓库替身的标记。"""
    fixtures: dict[str, Callable[..., Any]] = {}
    for module in modules:
        for name, obj in vars(module).items():
            if not callable(obj):
                continue
            is_fixture = (
                getattr(obj, "_booksoul_fixture", False)
                # pytest < 9 的标记名
                or hasattr(obj, "_pytestfixturefunction")
                # pytest 9+ 改成了这个名字（本项目 .venv 里是 9.1.1）
                or hasattr(obj, "_fixture_function_marker")
            )
            if is_fixture:
                fixtures[name] = obj
    return fixtures


def _resolve(fixtures: dict[str, Callable[..., Any]], name: str) -> Any:
    if name not in fixtures:
        raise LookupError(f"未定义的夹具: {name}")
    func = fixtures[name]
    kwargs = {
        param: _resolve(fixtures, param)
        for param in inspect.signature(func).parameters
    }
    return func(**kwargs)


def _make_fixtures() -> dict[str, Callable[..., Any]]:
    counter = itertools.count()

    def tmp_path() -> Path:
        # 注意：刻意不用 tempfile.mkdtemp —— 它建出来的目录带受限 ACL，
        # 在受限沙箱里连自己都写不进去。用普通 mkdir 即可（用例各自独立）。
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        path = TMP_ROOT / f"tmp-{os.getpid()}-{next(counter)}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def monkeypatch() -> MonkeyPatch:
        return MonkeyPatch()

    tmp_path._booksoul_fixture = True  # type: ignore[attr-defined]
    monkeypatch._booksoul_fixture = True  # type: ignore[attr-defined]
    return {"tmp_path": tmp_path, "monkeypatch": monkeypatch}


def _install_pytest_stub(*, force: bool = False) -> None:
    """把 `tests/_pytest_stub.py` 注册成 `pytest`。

    - 默认（`force=False`）：只在**没装 pytest** 时启用（真 pytest 已在 sys.modules）。
    - `force=True`：**无条件**用替身覆盖。`--local` 模式必须这样：真 pytest 包装过的
      夹具不允许被直接调用（会抛 "Fixture ... called directly"），而本运行器正是靠
      "直接调用夹具函数"来执行的 —— 两种语义不兼容，只能二选一。
    """
    if not force:
        try:
            import pytest  # noqa: F401,PLC0415
            return
        except ImportError:
            pass

    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    import _pytest_stub  # noqa: PLC0415

    sys.modules["pytest"] = _pytest_stub
    print(
        "使用内置替身运行（tests/_pytest_stub.py）。"
        if force
        else "未检测到 pytest，已启用 tests/_pytest_stub.py（见该文件顶部说明）。"
    )


def _expand_parametrize(spec: Any) -> list[dict[str, Any]]:
    """把替身记录的 `(argnames, argvalues)` 展开成一组 kwargs。

    没有标记时返回 `[{}]`（即"一个用例、无额外参数"）。
    """
    if not spec:
        return [{}]

    argnames, argvalues = spec
    if isinstance(argnames, str):
        names = [part.strip() for part in argnames.split(",") if part.strip()]
    else:
        names = [str(part).strip() for part in argnames]

    cases: list[dict[str, Any]] = []
    for value in argvalues:
        if len(names) == 1:
            values: tuple[Any, ...] = (value,)
        elif isinstance(value, (tuple, list)):
            values = tuple(value)
        else:
            values = (value,)
        cases.append(dict(zip(names, values)))
    return cases


def _run_local() -> int:
    os.environ.setdefault("PYTHONPATH", str(SRC_DIR))
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    _install_pytest_stub(force=True)

    module_paths = [TESTS_DIR / "conftest.py"]
    module_paths += sorted(p for p in TESTS_DIR.glob("test_*.py") if p.is_file())
    modules: list[Any] = []
    for path in module_paths:
        if not path.is_file():
            continue
        try:
            modules.append(_import_module(path))
        except Exception:  # pragma: no cover - 收集期错误要显式报出来
            print(f"[COLLECT ERROR] {path.name}")
            traceback.print_exc()
            return 1

    # 只让 test_*.py 里的用例执行（conftest 只贡献夹具）。
    test_modules = [m for m in modules if m.__name__.startswith("test_")]

    fixtures = {**_make_fixtures(), **_collect_fixtures(modules)}

    passed = failed = skipped = 0
    failures: list[str] = []

    for module in test_modules:
        for name, obj in vars(module).items():
            if not (name.startswith("test_") and callable(obj)):
                continue
            # 一个测试函数可能被 parametrize 展开成多个用例
            cases = _expand_parametrize(getattr(obj, "_booksoul_parametrize", None))
            for case in cases:
                label = f"{module.__name__}::{name}"
                if case:
                    label += "[" + ",".join(str(value)[:20] for value in case.values()) + "]"
                patch = MonkeyPatch()
                try:
                    kwargs: dict[str, Any] = {}
                    for param in inspect.signature(obj).parameters:
                        if param in case:
                            continue  # 该参数由 parametrize 提供
                        if param == "monkeypatch":
                            kwargs[param] = patch
                        else:
                            kwargs[param] = _resolve(fixtures, param)
                    obj(**kwargs, **case)
                except BaseException as exc:  # noqa: BLE001
                    # `pytest.skip` 抛的是 BaseException 子类（Skipped 继承 OutcomeException），
                    # 用 `except Exception` 抓不住 —— 早期版本因此整个运行器崩掉。
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    if type(exc).__name__ == "Skipped":
                        skipped += 1
                        print(f"SKIPPED {label}: {exc}")
                    else:
                        failed += 1
                        failures.append(label)
                        print(f"FAILED {label}: {type(exc).__name__}: {exc}")
                        traceback.print_exc()
                else:
                    passed += 1
                finally:
                    patch.undo()

    print(f"\n{'=' * 60}")
    total = passed + failed + skipped
    print(f"内置运行器：{passed} passed, {skipped} skipped, {failed} failed（共 {total} 个用例）")
    if failures:
        print("失败用例：")
        for label in failures:
            print(f"  - {label}")
    shutil.rmtree(TMP_ROOT / "__unused__", ignore_errors=True)
    return 1 if failed else 0


def _run_pytest(extra: list[str]) -> int:
    import pytest  # noqa: PLC0415  (故意延迟导入)

    return int(pytest.main([str(TESTS_DIR), *extra]))


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文断言失败信息会变乱码；强制 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass

    parser = argparse.ArgumentParser(description="运行 BookSoul 测试")
    parser.add_argument("--local", action="store_true", help="强制使用内置运行器")
    args, extra = parser.parse_known_args(argv)

    if args.local:
        return _run_local()
    try:
        return _run_pytest(extra)
    except ImportError:
        print("未检测到 pytest，回退到内置运行器（见本文件顶部说明）。\n")
        return _run_local()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
