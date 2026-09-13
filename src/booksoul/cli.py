"""书魂 BookSoul 命令行入口。

阶段 1 只有 `version` / `config`；阶段 2 起陆续补上真正的业务命令。
当前可用：

| 命令 | 阶段 | 说明 |
|---|---|---|
| `version` | 1 | 版本号 |
| `config` | 1 | 自检数据目录 / 模型 / Key 状态（不打印 Key 明文） |
| `ingest` | 2 | 解析 `.txt` → 章节，落盘 `data/novels/{book_id}.json` |
| `identify` | 3 | 逐章粗扫人名 → 候选人物列表 |
| `extract` / `chat` | 4 / 6 | 阶段 8 才补齐 |
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from booksoul import __version__
from booksoul.config import ENV_API_KEY, load_settings
from booksoul.storage import build_repositories

app = typer.Typer(
    name="booksoul",
    help="书魂 BookSoul —— 从小说里抽出立体角色，和书里的角色对话。",
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


@app.command("ingest")
def ingest(
    path: Path = typer.Argument(..., help="小说 .txt 文件路径"),
    encoding: str = typer.Option("", "--encoding", "-e", help="强制指定编码（默认自动探测）"),
    max_chapter_length: int = typer.Option(
        6000, "--max-chapter-length", help="单章超过此字数就再切块（0=不切）"
    ),
) -> None:
    """解析小说 txt 并落盘（阶段 2）。"""
    from booksoul.ingest import dump_novel, parse_file

    if not path.is_file():
        console.print(f"[red]找不到文件[/red]: {path}")
        raise typer.Exit(code=1)

    settings = load_settings()
    novel = parse_file(
        path,
        encoding=encoding or None,
        max_chapter_length=max_chapter_length or None,
    )
    repositories = build_repositories(settings)
    dump_novel(novel, repositories.novels)

    table = Table(title=f"{novel.book_id}（编码 {novel.encoding}）")
    table.add_column("#", justify="right")
    table.add_column("章节标题")
    table.add_column("字数", justify="right")
    for chapter in novel.chapters[:20]:
        table.add_row(str(chapter.index), chapter.title, str(len(chapter.content)))
    console.print(table)
    console.print(
        f"共 [bold]{len(novel.chapters)}[/bold] 章 / {novel.char_count} 字 → "
        f"[dim]data/novels/{novel.book_id}.json[/dim]"
    )


@app.command("identify")
def identify(
    book_id: str = typer.Argument(..., help="`ingest` 产出的 book_id"),
    top_n: int = typer.Option(15, "--top-n", "-n", help="取前 N 作为候选"),
    force: bool = typer.Option(False, "--force", help="忽略缓存，强制重跑"),
    merge: bool = typer.Option(True, "--merge/--no-merge", help="是否跑别名归并（Prompt 2）"),
) -> None:
    """逐章识别人名 → 候选人物列表（阶段 3）。"""
    from booksoul.extract import identify_candidates, merge_aliases
    from booksoul.ingest import from_novel_record
    from booksoul.llm import LLMClient

    settings = load_settings()
    repositories = build_repositories(settings)

    record = repositories.novels.load(book_id)
    if record is None:
        console.print(f"[red]没有这本书[/red]: {book_id}（先跑 `booksoul ingest`）")
        raise typer.Exit(code=1)

    novel = from_novel_record(record)
    client = LLMClient.from_settings(settings)

    with console.status(f"逐章识别中（共 {len(novel.chapters)} 章）……"):
        payload, report = identify_candidates(
            novel, client, repositories, top_n=top_n, force=force
        )

    table = Table(title=f"{book_id} 候选人物（{report.chapter_scanned} 章新扫 / {report.chapter_cached} 章命中缓存）")
    table.add_column("排名", justify="right")
    table.add_column("姓名")
    table.add_column("出现章数", justify="right")
    table.add_column("出现次数", justify="right")
    for rank, candidate in enumerate(payload.candidates, start=1):
        table.add_row(str(rank), candidate.name, str(candidate.chapter_count), str(candidate.mentions))
    console.print(table)

    if merge and payload.candidates:
        contexts = {c.name: c.contexts for c in payload.candidates}
        with console.status("别名归并中（Prompt 2）……"):
            groups = merge_aliases(client, contexts)
        if groups:
            console.print("\n[bold]别名归并[/bold]")
            for group in groups:
                reason = f"  [dim]({group.reason})[/dim]" if group.reason else ""
                console.print(f"  {group.main} ← {'、'.join(group.aliases)}{reason}")
        else:
            console.print("\n[dim]没有需要合并的别名[/dim]")

    usage = client.usage.as_dict()
    console.print(
        f"\n调用 {usage['calls']} 次（失败 {usage['failed_calls']}）｜"
        f"prompt {usage['prompt_tokens']} + completion {usage['completion_tokens']} "
        f"= {usage['total_tokens']} tokens（缓存命中 {usage['cached_tokens']}）"
    )
    console.print(f"[dim]产物：data/cards/{book_id}/candidates.json[/dim]")


def main() -> None:  # pragma: no cover - 入口包装
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
