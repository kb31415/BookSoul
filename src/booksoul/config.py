"""运行配置：环境变量 / 模型名 / 路径（`MVP_PLAN.md` 阶段 1）。

约定：

- API Key **只读环境变量** `DEEPSEEK_API_KEY`，不写进代码、不进 git。
- 配置在**使用时**读取（`load_settings()`），而不是 import 时固化 —— 便于测试
  与在同一进程里切换环境。
- `DATA_DIR` 可用环境变量覆盖，默认 `<repo>/data`。
- 取值顺序：**进程环境变量 → 平台回退**（Windows 用户级环境变量）。
  回退是为了绕开"环境变量只对新进程生效"这个坑：用户设完 Key，
  不必重启终端/编辑器就能用。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DATA_SUBDIRS",
    "DEFAULT_MODEL_NAME",
    "ENV_API_KEY",
    "ENV_DATA_DIR",
    "ENV_MODEL_NAME",
    "FALLBACK_PROVIDERS",
    "Settings",
    "env_value",
    "get_data_dir",
    "get_project_root",
    "load_settings",
    "require_api_key",
]

#: 阶段 3 起的主力模型（LiteLLM 路由；换模型 = 改配置）。
DEFAULT_MODEL_NAME = "deepseek/deepseek-chat"

ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_MODEL_NAME = "MODEL_NAME"
ENV_DATA_DIR = "DATA_DIR"

#: 数据目录下的固定子目录（阶段 2 起各阶段产物落盘位置）。
DATA_SUBDIRS = ("novels", "cards", "sessions", "memory")

#: 读取环境变量的函数签名（便于测试与后续换实现）。
EnvProvider = Callable[[str], str | None]


def _windows_user_env(name: str) -> str | None:
    """读 Windows **用户级**环境变量（`SetEnvironmentVariable(..., "User")` 写的那层）。

    为什么需要：环境变量只对**新启动的进程**可见。用户设好 Key 之后，
    已经在跑的终端 / 编辑器里 `os.environ` 仍然看不到它。这里补一层回退，
    让"设完就能用"。非 Windows 或读取失败一律返回 `None`（不影响主流程）。
    """
    if os.name != "nt":  # pragma: no cover - 仅在非 Windows 上分支
        return None
    try:
        import winreg  # noqa: PLC0415 - 只在 Windows 上需要
    except ImportError:  # pragma: no cover
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def _fallback_providers() -> tuple[EnvProvider, ...]:
    """进程环境变量之后，依次尝试的回退来源。

    模块级常量 `FALLBACK_PROVIDERS` 存在的意义：**测试要能把回退关掉**，
    否则有用户级 Key 的机器上，"缺 Key"的用例会读到真实 Key 而失败。
    显式传 `()` 即可关掉（见 `tests/conftest.py` 的 `no_env_fallback`）。
    """
    return FALLBACK_PROVIDERS


def _default_fallback_providers() -> tuple[EnvProvider, ...]:
    if os.name == "nt":
        return (_windows_user_env,)
    return ()


#: 回退来源；测试可替换为空元组以隔离真实环境。
FALLBACK_PROVIDERS: tuple[EnvProvider, ...] = _default_fallback_providers()


def env_value(name: str) -> str:
    """按「进程环境变量 → 平台回退」取一个环境变量，返回 `str`（没有则 `""`）。

    `load_settings()` / `get_data_dir()` 都走这里，保证行为一致。
    """
    value = os.environ.get(name)
    if value:
        return value.strip()

    for provider in _fallback_providers():
        try:
            fallback = provider(name)
        except Exception:  # noqa: BLE001 - 回退失败绝不能影响主流程
            continue
        if fallback:
            return fallback.strip()
    return ""


def get_project_root() -> Path:
    """仓库根目录（`src/booksoul/config.py` 往上三层）。"""
    return Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """数据根目录：环境变量 `DATA_DIR` 优先，否则 `<repo>/data`。"""
    override = env_value(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    return get_project_root() / "data"


@dataclass(frozen=True)
class Settings:
    """一次运行所需的全部配置。"""

    api_key: str = ""
    model_name: str = DEFAULT_MODEL_NAME
    data_dir: Path = field(default_factory=get_data_dir)

    # ── 派生路径 ──

    @property
    def novels_dir(self) -> Path:
        return self.data_dir / "novels"

    @property
    def cards_dir(self) -> Path:
        return self.data_dir / "cards"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    # ── 行为 ──

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def character_dir(self, book_id: str) -> Path:
        """某本书的抽取产物目录：`data/cards/{book_id}/`。"""
        return self.cards_dir / book_id

    def ensure_dirs(self) -> Settings:
        """建好所有数据目录（幂等），返回自身便于链式调用。"""
        for path in (self.data_dir, *(self.data_dir / name for name in DATA_SUBDIRS)):
            path.mkdir(parents=True, exist_ok=True)
        return self


def load_settings(
    *,
    api_key: str | None = None,
    model_name: str | None = None,
    data_dir: str | Path | None = None,
) -> Settings:
    """从环境变量装载配置；显式传参优先（便于测试与 CLI 覆盖）。"""
    resolved_key = api_key if api_key is not None else env_value(ENV_API_KEY)
    resolved_model = model_name if model_name is not None else env_value(ENV_MODEL_NAME)
    if data_dir is None:
        resolved_data = get_data_dir()
    else:
        resolved_data = Path(data_dir).expanduser()

    return Settings(
        api_key=resolved_key.strip(),
        model_name=resolved_model.strip() or DEFAULT_MODEL_NAME,
        data_dir=resolved_data,
    )


def require_api_key(settings: Settings | None = None) -> str:
    """取 API Key；缺失时给出可操作的报错（阶段 3 起才真正需要）。"""
    current = settings or load_settings()
    if not current.api_key:
        raise RuntimeError(
            f"未设置 {ENV_API_KEY}。请先设置环境变量，例如：\n"
            f'  PowerShell:  $env:{ENV_API_KEY} = "sk-..."\n'
            f'  bash:        export {ENV_API_KEY}="sk-..."\n'
            "（Key 只从环境变量读取，不会写入代码或提交到 git）"
        )
    return current.api_key
