"""运行配置：环境变量 / 模型名 / 路径（`MVP_PLAN.md` 阶段 1）。

约定：

- API Key **只读环境变量** `DEEPSEEK_API_KEY`，不写进代码、不进 git。
- 配置在**使用时**读取（`load_settings()`），而不是 import 时固化 —— 便于测试
  与在同一进程里切换环境。
- `DATA_DIR` 可用环境变量覆盖，默认 `<repo>/data`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_MODEL_NAME",
    "ENV_API_KEY",
    "ENV_DATA_DIR",
    "ENV_MODEL_NAME",
    "Settings",
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


def get_project_root() -> Path:
    """仓库根目录（`src/booksoul/config.py` 往上三层）。"""
    return Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """数据根目录：环境变量 `DATA_DIR` 优先，否则 `<repo>/data`。"""
    override = os.environ.get(ENV_DATA_DIR, "").strip()
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
    resolved_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY, "")
    resolved_model = model_name if model_name is not None else os.environ.get(
        ENV_MODEL_NAME, ""
    )
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
