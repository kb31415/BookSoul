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
    from booksoul.extract import (
        collect_same_reference_evidence,
        forbidden_merges,
        identify_candidates,
        merge_aliases,
    )
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
    table.add_column("上下文", justify="right")
    for rank, candidate in enumerate(payload.candidates, start=1):
        table.add_row(
            str(rank),
            candidate.name,
            str(candidate.chapter_count),
            str(candidate.mentions),
            str(len(candidate.contexts)),
        )
    console.print(table)

    if report.renamed:
        console.print("\n[bold]字形归一（按原文校正）[/bold]")
        for source, target in report.renamed.items():
            console.print(f"  {source} → {target}")

    if merge and payload.candidates:
        names = [candidate.name for candidate in payload.candidates]
        contexts = {candidate.name: candidate.contexts for candidate in payload.candidates}
        # 确定性证据（都不问 LLM）：
        #   异指 —— 亲属关系句（"A 的大兒子叫做 B"）→ 禁止合并
        #   同指 —— 分级：强标记（即是/贱字/小名）强制合并；弱标记（就是）只作提示
        forbidden = forbidden_merges(names, novel.chapters)
        evidence = collect_same_reference_evidence(names, novel.chapters, forbidden=forbidden)
        with console.status("别名归并中（Prompt 2）……"):
            groups, notes = merge_aliases(
                client,
                contexts,
                forbidden=forbidden,
                same_reference=evidence,
            )
        if evidence.forced:
            console.print("\n[bold]同指强证据[/bold]（几乎不可能误判的句式 → 强制合并）")
            for pair, proof in evidence.forced.items():
                left, right = sorted(pair)
                console.print(f"  {left} = {right}   [dim]{proof}[/dim]")
        if evidence.hints:
            console.print("\n[bold]同指弱证据[/bold]（如「就是」→ 交给 LLM 结合上下文裁决）")
            for pair, proof in evidence.hints.items():
                left, right = sorted(pair)
                console.print(f"  {left} ?= {right}   [dim]{proof}[/dim]")
        if groups:
            console.print("\n[bold]别名归并[/bold]")
            for group in groups:
                reason = f"  [dim]({group.reason})[/dim]" if group.reason else ""
                console.print(f"  {group.main} ← {'、'.join(group.aliases)}{reason}")
        else:
            console.print("\n[dim]没有需要合并的别名[/dim]")
        for note in notes:
            console.print(f"  [yellow]守卫[/yellow] {note}")

    usage = client.usage.as_dict()
    console.print(
        f"\n调用 {usage['calls']} 次（失败 {usage['failed_calls']}）｜"
        f"prompt {usage['prompt_tokens']} + completion {usage['completion_tokens']} "
        f"= {usage['total_tokens']} tokens（缓存命中 {usage['cached_tokens']}）"
    )
    console.print(f"[dim]产物：data/cards/{book_id}/candidates.json[/dim]")


@app.command("extract")
def extract(
    book_id: str = typer.Argument(..., help="`ingest` 产出的 book_id"),
    character: str = typer.Argument(..., help="角色主名（同 `identify` 结果里的名字）"),
    aliases: str = typer.Option("", "--aliases", "-a", help="别名，逗号分隔（强烈建议给：检索召回靠它）"),
    max_passage_chars: int = typer.Option(5000, "--max-passage-chars", help="单次喂入的相关段落上限"),
    generate: bool = typer.Option(True, "--generate/--no-generate", help="是否跑 Prompt 4 生成 first_mes"),
    reuse: bool = typer.Option(
        False,
        "--reuse",
        help="复用已落盘的 persona（跳过逐章抽取，只补跑 Prompt 4）—— 不产生逐章调用",
    ),
) -> None:
    """抽取指定角色的立体字段（阶段 4，🔴 风险点）。"""
    from rich.panel import Panel

    from booksoul.assemble import (
        MergedPersona,
        PersonaIncrement,
        PersonaTimeline,
        TimelinePoint,
        relationships_by_target,
    )
    from booksoul.extract import ExtractionReport, extract_character, generate_card_fields
    from booksoul.ingest import from_novel_record
    from booksoul.llm import LLMClient

    settings = load_settings()
    repositories = build_repositories(settings)

    record = repositories.novels.load(book_id)
    if record is None:
        console.print(f"[red]没有这本书[/red]: {book_id}（先跑 `booksoul ingest`）")
        raise typer.Exit(code=1)

    novel = from_novel_record(record)
    alias_list = [item.strip() for item in aliases.split(",") if item.strip()]
    client = LLMClient.from_settings(settings)

    if reuse:
        # 复用已落盘的 persona：**跳过**逐章抽取（那部分是花钱大头），
        # 只补跑 Prompt 4。适用于"persona 已经抽好、只缺 first_mes"的场景。
        cached = repositories.cards.load_json(book_id, f"{character}.persona")
        if not isinstance(cached, dict) or "persona" not in cached:
            console.print(f"[red]没有可复用的 persona[/red]：{character}.persona.json")
            raise typer.Exit(code=1)

        persona = PersonaIncrement.model_validate(cached["persona"])
        timeline = PersonaTimeline(
            points=[TimelinePoint(**point) for point in (cached.get("timeline") or [])]
        )
        merged = MergedPersona(persona=persona, timeline=timeline)
        report = ExtractionReport(
            character_name=character,
            chapter_total=cached.get("extraction_report", {}).get("chapter_total", 0),
            chapter_hit=cached.get("extraction_report", {}).get("chapter_hit", 0),
        )
        console.print("[dim]已复用落盘 persona（跳过逐章抽取）[/dim]")
    else:
        with console.status(f"逐章抽取「{character}」的 persona 增量……"):
            merged, report = extract_character(
                novel, client, character, alias_list, max_passage_chars=max_passage_chars
            )

    persona = merged.persona
    table = Table(title=f"{character} —— persona（{report.chapter_hit}/{report.chapter_total} 章命中）")
    table.add_column("字段")
    table.add_column("内容")
    table.add_column("长度", justify="right")
    labels = {
        "personality": "性格",
        "desire": "欲望",
        "flaw": "缺陷",
        "secret": "秘密",
        "speech_style": "说话风格",
    }
    for name, label in labels.items():
        value = getattr(persona, name)
        table.add_row(label, value or "[dim]（空）[/dim]", str(len(value)))
    console.print(table)

    if persona.relationships:
        console.print("\n[bold]关系变化[/bold]")
        for target, changes in relationships_by_target(persona).items():
            console.print(f"  {target}：{' → '.join(changes)}")

    if persona.speech_samples:
        console.print("\n[bold]原话素材[/bold]")
        for sample in persona.speech_samples[:8]:
            console.print(f"  · {sample}")

    if persona.quotes:
        console.print(f"\n[bold]原文依据[/bold]（{len(persona.quotes)} 条）")
        for quote in persona.quotes[:8]:
            console.print(f"  [{quote.confidence}] {quote.field}：{quote.text[:40]}")

    if merged.timeline.points:
        console.print("\n[bold]时间线（成长弧光素材）[/bold]")
        for point in merged.timeline.points[:8]:
            arrow = f"{point.previous} → " if point.previous else ""
            console.print(f"  第 {point.chapter_index} 章 {point.field}：{arrow}{point.value[:30]}")

    if report.has_problems:
        console.print("\n[yellow]质量校验告警[/yellow]")
        if report.cleared_fields:
            console.print(f"  置空（无原文依据）：{'、'.join(report.cleared_fields)}")
        if report.ungrounded_fields:
            console.print(f"  引用未落实（保留待人工确认）：{'、'.join(report.ungrounded_fields)}")
        if report.dropped_quotes:
            console.print(f"  丢弃假引用 {len(report.dropped_quotes)} 条：{report.dropped_quotes[0][:50]}")
        if report.cliches:
            console.print(f"  套话：{'；'.join(report.cliches)}")
        if report.field_length_warnings:
            console.print(f"  长度越界：{'；'.join(report.field_length_warnings)}")

    if generate:
        with console.status("生成 first_mes / mes_example（Prompt 4）……"):
            fields = generate_card_fields(client, character, persona)
        console.print(Panel(fields.first_mes or "[dim]（空）[/dim]", title="first_mes（开场白 / 钩子）"))
        console.print(Panel(fields.mes_example or "[dim]（空）[/dim]", title="mes_example（原话 few-shot）"))
    else:
        fields = None

    # 中间产物落盘（`PROMPT_DESIGN.md` §11 / `MVP_PLAN.md` §1：产物路径固定，
    # 便于后续阶段复用与人工检查；也避免"跑完了但东西只在终端里"）。
    payload = {
        "character": character,
        "aliases": alias_list,
        "book_id": book_id,
        "model": client.model,
        "persona": persona.model_dump(mode="json"),
        "timeline": merged.timeline.as_dicts(),
        "card_fields": fields.model_dump(mode="json") if fields else None,
        "extraction_report": report.as_dict(),
    }
    target = repositories.cards.save_json(book_id, f"{character}.persona", payload)
    console.print(f"\n[dim]产物：{target}[/dim]")

    usage = client.usage.as_dict()
    console.print(
        f"\n调用 {usage['calls']} 次（失败 {usage['failed_calls']}）｜"
        f"prompt {usage['prompt_tokens']} + completion {usage['completion_tokens']} "
        f"= {usage['total_tokens']} tokens（缓存命中 {usage['cached_tokens']}）"
    )
    console.print(
        "[yellow]人工把关点[/yellow]：阶段 4 是 MVP 第一风险点 —— "
        "请检查上面的字段是否「像书中人」（不是通用套话）。"
    )


@app.command("build")
def build(
    book_id: str = typer.Argument(..., help="`ingest` 产出的 book_id"),
    character: str = typer.Argument(..., help="角色主名（同 `extract` 用过的名字）"),
    tags: str = typer.Option("", "--tags", "-t", help="额外标签，逗号分隔"),
) -> None:
    """组装完整角色卡并导出酒馆 v2 JSON（阶段 5，纯本地不调 API）。"""
    from booksoul.assemble import (
        MergedPersona,
        PersonaIncrement,
        PersonaTimeline,
        TimelinePoint,
        build_card,
        card_to_v2_payload,
    )

    settings = load_settings()
    repositories = build_repositories(settings)

    cached = repositories.cards.load_json(book_id, f"{character}.persona")
    if not isinstance(cached, dict) or "persona" not in cached:
        console.print(f"[red]没有可用的人设产物[/red]：{book_id}/{character}.persona.json（先跑 `extract`）")
        raise typer.Exit(code=1)

    merged = MergedPersona(
        persona=PersonaIncrement.model_validate(cached["persona"]),
        timeline=PersonaTimeline(
            points=[TimelinePoint(**point) for point in (cached.get("timeline") or [])]
        ),
    )
    card_fields = cached.get("card_fields") or {}

    card, report = build_card(
        character,
        merged,
        book_id=book_id,
        source_book=book_id,
        first_mes=str(card_fields.get("first_mes", "") or ""),
        mes_example=str(card_fields.get("mes_example", "") or ""),
        tags=[item.strip() for item in tags.split(",") if item.strip()],
    )

    # ① 完整卡片（含 timeline / relations / quotes 等全部立体信息）
    card_path = repositories.cards.save_json(book_id, f"{character}.card", card.model_dump(mode="json"))
    # ② 酒馆 v2 导出（可直接拖进 SillyTavern）
    v2_payload = card_to_v2_payload(card)
    v2_path = repositories.cards.save_json(book_id, f"{character}.v2", v2_payload)

    table = Table(title=f"{character} —— 角色卡（阶段 5）")
    table.add_column("字段")
    table.add_column("内容")
    table.add_column("长度", justify="right")
    previews = (
        ("name", card.name),
        ("description", card.description.splitlines()[0] if card.description else ""),
        ("personality", card.personality),
        ("desire", card.desire),
        ("flaw", card.flaw),
        ("secret", card.secret),
        ("speech_style", card.speech_style),
        ("first_mes", card.first_mes),
        ("mes_example", card.mes_example.replace("\n", " ⏎ ")),
    )
    for label, value in previews:
        table.add_row(label, value or "[dim]（空）[/dim]", str(len(value)))
    console.print(table)

    console.print(
        f"\n[bold]立体信息[/bold]：relations {report.relation_count} 条"
        f"（演化 {report.relation_evolution_total} 次）｜"
        f"timeline {report.timeline_count} 个点｜"
        f"quotes {report.quote_count} 条｜原话素材 {report.speech_sample_count} 条"
    )
    console.print(f"tags：{'、'.join(card.tags)}")

    if report.missing_required:
        console.print(f"[red]必填字段缺失[/red]：{'、'.join(report.missing_required)}")
    for warning in report.warnings:
        console.print(f"  [yellow]告警[/yellow] {warning}")
    if report.ok and not report.warnings:
        console.print("[green]格式校验通过[/green]")
    elif report.ok:
        console.print("[yellow]格式校验通过（有告警）[/yellow]")

    console.print(f"\n[dim]完整卡片：{card_path}[/dim]")
    console.print(f"[dim]酒馆 v2  ：{v2_path}[/dim]")
    console.print("[dim]人工检查：把 v2 文件拖进 SillyTavern，或让格式校验器过一遍[/dim]")


def main() -> None:  # pragma: no cover - 入口包装
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
