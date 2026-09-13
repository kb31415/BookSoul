"""书魂 BookSoul 命令行入口。

阶段 1 只提供 `version` / `config` 两个自检子命令；阶段 8 才补齐
`ingest` / `identify` / `extract` / `chat`（见 `MVP_PLAN.md` 阶段 8）。
"""

from __future__ import annotations

import typer
from rich.console import Console

from booksoul import __version__
from booksoul.config import ENV_API_KEY, load_settings

app = typer.Typer(
    name="booksoul",
    help="书魂 BookSoul —— 从小说里抽出立体角色，并和书里的角色对话。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command("version")
def version() -> None:
    """打印版本号。"""
    console.print(f"booksoul {__version__}")


@app.command("config")
def show_config() -> None:
    """自检：打印生效的数据目录与模型（不打印 API Key 明文）。"""
    settings = load_settings()
    key_state = "已设置" if settings.has_api_key else f"未设置（读环境变量 {ENV_API_KEY}）"
    console.print(f"[bold]数据目录[/bold]: {settings.data_dir}")
    console.print(f"[bold]模型[/bold]    : {settings.model_name}")
    console.print(f"[bold]API Key[/bold] : {key_state}")


def main() -> None:  # pragma: no cover - 入口包装
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
