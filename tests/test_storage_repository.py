"""Repository 测试（`MVP_PLAN.md` 阶段 1 任务 5–6「Repository CRUD」）。

结构红线（`HANDOFF.md` §2）要求业务代码只依赖接口，所以这里既测四个文件实现的
CRUD 行为，也测**接口契约本身**（`isinstance` 检查 Protocol 是否被满足）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from booksoul.config import Settings
from booksoul.schema import CharacterCard, Relation
from booksoul.storage import (
    CardRepository,
    FileCardRepository,
    FileMemoryRepository,
    FileNovelRepository,
    FileSessionRepository,
    MemoryEntry,
    MemoryRepository,
    NovelRecord,
    NovelRepository,
    RepositorySet,
    SessionLog,
    SessionRepository,
    build_repositories,
    content_id,
)
from conftest import make_full_card


@pytest.fixture()
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RepositorySet:
    """一套指向 tmp 的文件版 Repository（不碰真实 data/）。"""
    settings = Settings(data_dir=tmp_path)
    return build_repositories(settings)


# ══════════════════════════ 装配与接口契约 ══════════════════════════


def test_build_repositories_wires_all_four(repos: RepositorySet) -> None:
    assert isinstance(repos.novels, FileNovelRepository)
    assert isinstance(repos.cards, FileCardRepository)
    assert isinstance(repos.memory, FileMemoryRepository)
    assert isinstance(repos.sessions, FileSessionRepository)


def test_file_implementations_satisfy_protocols(repos: RepositorySet) -> None:
    """结构红线 ① 的核心：实现必须满足接口（换实现时上层不改）。"""
    assert isinstance(repos.novels, NovelRepository)
    assert isinstance(repos.cards, CardRepository)
    assert isinstance(repos.memory, MemoryRepository)
    assert isinstance(repos.sessions, SessionRepository)


def test_build_repositories_uses_settings_dirs(tmp_path: Path) -> None:
    repos = build_repositories(Settings(data_dir=tmp_path))

    assert repos.novels.root == tmp_path / "novels"
    assert repos.cards.root == tmp_path / "cards"
    assert repos.memory.root == tmp_path / "memory"
    assert repos.sessions.root == tmp_path / "sessions"


def test_build_repositories_creates_directories(tmp_path: Path) -> None:
    build_repositories(Settings(data_dir=tmp_path / "fresh"))

    for name in ("novels", "cards", "memory", "sessions"):
        assert (tmp_path / "fresh" / name).is_dir()


def test_ensure_dirs_is_chainable(repos: RepositorySet) -> None:
    assert repos.ensure_dirs() is repos


# ══════════════════════════ NovelRepository ══════════════════════════


def _record(book_id: str = "青云旧事", chapters: int = 6) -> NovelRecord:
    return NovelRecord(
        book_id=book_id,
        source_path=f"D:/story/data/novels/{book_id}.txt",
        encoding="utf-8",
        char_count=2531,
        chapter_count=chapters,
        payload={"book_id": book_id, "chapters": [{"index": 0, "title": "第一章", "content": "正文"}]},
    )


def test_novel_save_and_load(repos: RepositorySet) -> None:
    saved_id = repos.novels.save(_record())

    loaded = repos.novels.load("青云旧事")

    assert saved_id == "青云旧事"
    assert loaded is not None
    assert loaded.char_count == 2531
    assert loaded.chapter_count == 6
    assert loaded.payload["chapters"][0]["title"] == "第一章"


def test_novel_load_missing_returns_none(repos: RepositorySet) -> None:
    assert repos.novels.load("不存在") is None
    assert repos.novels.exists("不存在") is False


def test_novel_exists_and_list(repos: RepositorySet) -> None:
    repos.novels.save(_record("甲"))
    repos.novels.save(_record("乙"))

    assert repos.novels.exists("甲") is True
    # list_ids 按 Unicode 码点排序（不引第三方排序库）
    assert repos.novels.list_ids() == sorted(["甲", "乙"])


def test_novel_list_on_empty_root(repos: RepositorySet) -> None:
    assert repos.novels.list_ids() == []


def test_novel_delete(repos: RepositorySet) -> None:
    repos.novels.save(_record())

    assert repos.novels.delete("青云旧事") is True
    assert repos.novels.load("青云旧事") is None
    assert repos.novels.delete("青云旧事") is False  # 再删返回 False


def test_novel_corrupt_file_is_tolerated(repos: RepositorySet) -> None:
    """坏文件不能让读取炸掉（宽容读取）。"""
    (repos.novels.root / "坏书.json").write_text("{ 不是合法 JSON", encoding="utf-8")

    assert repos.novels.load("坏书") is None
    assert "坏书" in repos.novels.list_ids()


def test_repository_rejects_path_traversal(repos: RepositorySet) -> None:
    """id 里带 `../` 不能写到目录外去。"""
    repos.novels.save(_record("../../逃逸"))

    written = list(repos.novels.root.glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == repos.novels.root
    assert not (repos.novels.root.parent.parent / "逃逸.json").exists()


# ══════════════════════════ CardRepository ══════════════════════════


def test_card_save_and_load(repos: RepositorySet) -> None:
    card = make_full_card()

    saved_id = repos.cards.save(card)
    loaded = repos.cards.load("沈知舟")

    assert saved_id == "沈知舟"
    assert loaded == card
    assert loaded is not None and isinstance(loaded.relations[0], Relation)


def test_card_id_defaults_to_name(repos: RepositorySet) -> None:
    assert repos.cards.save(make_full_card()) == "沈知舟"


def test_card_explicit_id_wins(repos: RepositorySet) -> None:
    assert repos.cards.save(make_full_card(), character_id="char_001") == "char_001"
    assert repos.cards.exists("char_001") is True
    assert repos.cards.exists("沈知舟") is False


def test_card_load_missing_returns_none(repos: RepositorySet) -> None:
    assert repos.cards.load("没有这张卡") is None


def test_card_list_and_delete(repos: RepositorySet) -> None:
    repos.cards.save(make_full_card())
    repos.cards.save(CharacterCard(name="甲", description="乙"))

    assert repos.cards.list_ids() == sorted(["沈知舟", "甲"])

    assert repos.cards.delete("甲") is True
    assert repos.cards.list_ids() == ["沈知舟"]


def test_card_round_trips_through_storage(repos: RepositorySet) -> None:
    """存进 Repository 再读出来，导出的 v2 JSON 必须一模一样。"""
    card = make_full_card()
    repos.cards.save(card)

    loaded = repos.cards.load("沈知舟")

    assert loaded is not None
    assert loaded.to_tavern_v2() == card.to_tavern_v2()


def test_card_tavern_v2_sidecar(repos: RepositorySet) -> None:
    """阶段 5 要落盘的 v2 导出版本。"""
    card = make_full_card()

    repos.cards.save_tavern_v2(card)
    loaded = repos.cards.load_tavern_v2("沈知舟")

    assert loaded == card
    assert (repos.cards.root / "沈知舟.v2.json").is_file()


def test_card_rejects_empty_id(repos: RepositorySet) -> None:
    with pytest.raises(ValueError):
        repos.cards.save(CharacterCard(name="", description="没有名字"))


# ══════════════════════════ MemoryRepository ══════════════════════════


def _memory(entry_id: str = "", character_id: str = "沈知舟", content: str = "他习惯右手执剑。") -> MemoryEntry:
    return MemoryEntry(id=entry_id, character_id=character_id, content=content)


def test_memory_add_and_list(repos: RepositorySet) -> None:
    memory_id = repos.memory.add(_memory())

    entries = repos.memory.list_by_character("沈知舟")

    assert len(entries) == 1
    assert entries[0].id == memory_id
    assert entries[0].tier == "heuristic"  # MVP 全填 heuristic
    assert entries[0].source == "dialogue"
    assert entries[0].content == "他习惯右手执剑。"


def test_memory_is_entry_shaped_not_bare_messages(repos: RepositorySet) -> None:
    """结构红线 ③：记忆必须是条目结构（否则第二阶段要重写记忆系统）。"""
    repos.memory.add(_memory())

    entry = repos.memory.list_by_character("沈知舟")[0]

    for field in ("id", "tier", "content", "source", "hits", "corrected", "conflicts_with"):
        assert hasattr(entry, field), field
    assert entry.created_at  # 时间戳也要有


def test_memory_add_is_deduplicated(repos: RepositorySet) -> None:
    """同内容重复写入不产生第二条（返回既有 id）。"""
    first = repos.memory.add(_memory(content="同一件事"))
    second = repos.memory.add(_memory(content="同一件事"))

    assert first == second
    assert len(repos.memory.list_by_character("沈知舟")) == 1


def test_memory_different_content_appends(repos: RepositorySet) -> None:
    repos.memory.add(_memory(content="甲"))
    repos.memory.add(_memory(content="乙"))

    assert len(repos.memory.list_by_character("沈知舟")) == 2


def test_memory_explicit_id_replaces_content(repos: RepositorySet) -> None:
    """人工修正场景：同 id 不同内容 → 原地替换，不新增。"""
    repos.memory.add(_memory(entry_id="mem_fixed", content="旧"))
    repos.memory.add(_memory(entry_id="mem_fixed", content="新"))

    entries = repos.memory.list_by_character("沈知舟")

    assert len(entries) == 1
    assert entries[0].content == "新"


def test_memory_touch_increments_hits(repos: RepositorySet) -> None:
    memory_id = repos.memory.add(_memory())

    touched = repos.memory.touch("沈知舟", memory_id)

    assert touched is not None and touched.hits == 1
    assert repos.memory.list_by_character("沈知舟")[0].hits == 1


def test_memory_touch_unknown_id_returns_none(repos: RepositorySet) -> None:
    repos.memory.add(_memory())

    assert repos.memory.touch("沈知舟", "mem_不存在") is None


def test_memory_touch_missing_character_returns_none(repos: RepositorySet) -> None:
    assert repos.memory.touch("没人", "mem_x") is None


def test_memory_requires_character_id(repos: RepositorySet) -> None:
    with pytest.raises(ValueError):
        repos.memory.add(MemoryEntry(content="没有归属"))


def test_memory_character_ids_and_clear(repos: RepositorySet) -> None:
    repos.memory.add(_memory(character_id="甲", content="A"))
    repos.memory.add(_memory(character_id="乙", content="B"))

    assert repos.memory.character_ids() == ["乙", "甲"]
    assert repos.memory.clear("甲") == 1
    assert repos.memory.character_ids() == ["乙"]


def test_memory_list_missing_character_is_empty(repos: RepositorySet) -> None:
    assert repos.memory.list_by_character("没人") == []


def test_memory_bad_line_is_skipped(repos: RepositorySet) -> None:
    """坏行只丢那一行，不废掉整个文件。"""
    repos.memory.add(_memory(content="好的一行"))
    path = repos.memory.root / "沈知舟.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ 这不是 JSON\n")
        handle.write("\n")
    repos.memory.add(_memory(content="另一行"))

    contents = [entry.content for entry in repos.memory.list_by_character("沈知舟")]

    assert contents == ["好的一行", "另一行"]


def test_content_id_is_stable_and_scoped() -> None:
    assert content_id("甲", "内容", "dialogue") == content_id("甲", "内容", "dialogue")
    assert content_id("甲", "内容", "dialogue") != content_id("乙", "内容", "dialogue")
    assert content_id("甲", "内容", "dialogue").startswith("mem_")


# ══════════════════════════ SessionRepository ══════════════════════════


def test_session_create_and_load(repos: RepositorySet) -> None:
    created = repos.sessions.create("sess_001", character_id="沈知舟")
    loaded = repos.sessions.load("sess_001")

    assert created.id == "sess_001"
    assert loaded == created
    assert loaded is not None
    assert loaded.character_id == "沈知舟"
    assert loaded.messages == []


def test_session_has_reserved_state_field(repos: RepositorySet) -> None:
    """结构红线 ④：session 必须有 `state: dict = {}`（供后续 mood / 剧情用）。"""
    session = repos.sessions.create("sess_001")

    assert session.state == {}
    assert isinstance(session.state, dict)


def test_session_state_can_be_set_and_read_back(repos: RepositorySet) -> None:
    """`state` 不是摆设 —— 写进去要能读回来（否则第二阶段等于没有）。"""
    repos.sessions.create("sess_001")

    updated = repos.sessions.update_state("sess_001", {"mood": "calm", "chapter": 3})

    assert updated is not None and updated.state == {"mood": "calm", "chapter": 3}
    loaded = repos.sessions.load("sess_001")
    assert loaded is not None and loaded.state == {"mood": "calm", "chapter": 3}


def test_session_update_state_missing_returns_none(repos: RepositorySet) -> None:
    assert repos.sessions.update_state("没有这个会话", {"a": 1}) is None


def test_session_append_message(repos: RepositorySet) -> None:
    repos.sessions.create("sess_001")

    repos.sessions.append_message("sess_001", {"role": "user", "content": "师兄"})
    final = repos.sessions.append_message("sess_001", {"role": "assistant", "content": "手滑。"})

    assert len(final.messages) == 2
    assert final.messages[0]["role"] == "user"
    assert final.messages[1]["content"] == "手滑。"


def test_session_append_message_with_state(repos: RepositorySet) -> None:
    repos.sessions.create("sess_001")

    final = repos.sessions.append_message(
        "sess_001", {"role": "user", "content": "甲"}, state={"mood": "wary"}
    )

    assert final.state == {"mood": "wary"}
    loaded = repos.sessions.load("sess_001")
    assert loaded is not None and loaded.state == {"mood": "wary"}


def test_session_append_to_missing_session_creates_it(repos: RepositorySet) -> None:
    """容错：直接追加也能用（CLI 不必先 create）。"""
    final = repos.sessions.append_message("sess_new", {"role": "user", "content": "在吗"})

    assert final.id == "sess_new"
    assert repos.sessions.load("sess_new") is not None


def test_session_append_preserves_state_when_not_given(repos: RepositorySet) -> None:
    repos.sessions.create("sess_001")
    repos.sessions.update_state("sess_001", {"mood": "calm"})

    final = repos.sessions.append_message("sess_001", {"role": "user", "content": "甲"})

    assert final.state == {"mood": "calm"}


def test_session_append_rejects_non_dict(repos: RepositorySet) -> None:
    with pytest.raises(TypeError):
        repos.sessions.append_message("sess_001", "不是 dict")  # type: ignore[arg-type]


def test_session_updated_at_changes_on_append(repos: RepositorySet) -> None:
    created = repos.sessions.create("sess_001")

    final = repos.sessions.append_message("sess_001", {"role": "user", "content": "甲"})

    assert final.updated_at >= created.updated_at


def test_session_load_missing_returns_none(repos: RepositorySet) -> None:
    assert repos.sessions.load("没有这个会话") is None


def test_session_list_and_delete(repos: RepositorySet) -> None:
    repos.sessions.create("sess_a")
    repos.sessions.create("sess_b")

    assert repos.sessions.list_ids() == ["sess_a", "sess_b"]
    assert repos.sessions.delete("sess_a") is True
    assert repos.sessions.list_ids() == ["sess_b"]
    assert repos.sessions.delete("sess_a") is False


def test_session_jsonl_is_append_only_on_disk(repos: RepositorySet) -> None:
    """落盘形式：meta 一行 + 每条消息一行（设计 §13.6 append-only）。"""
    repos.sessions.create("sess_001")
    repos.sessions.append_message("sess_001", {"role": "user", "content": "甲"})
    repos.sessions.append_message("sess_001", {"role": "assistant", "content": "乙"})

    lines = [
        line
        for line in (repos.sessions.root / "sess_001.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]

    assert len(lines) == 3
    assert '"kind": "meta"' in lines[0]
    assert '"kind": "message"' in lines[1]


def test_session_corrupt_meta_is_tolerated(repos: RepositorySet) -> None:
    (repos.sessions.root / "坏会话.jsonl").write_text("不是 JSON\n", encoding="utf-8")

    assert repos.sessions.load("坏会话") is None


def test_session_log_round_trips_through_model() -> None:
    session = SessionLog(id="s", character_id="c", state={"mood": "calm"}, messages=[{"role": "user"}])

    restored = SessionLog.model_validate(session.model_dump(mode="json"))

    assert restored == session


# ══════════════════════════ 跨 Repository 的隔离 ══════════════════════════


def test_repositories_do_not_share_files(repos: RepositorySet) -> None:
    """四类数据的文件互不干扰（路径固定，见 MVP_PLAN §1）。"""
    repos.novels.save(_record("同名"))
    repos.cards.save(CharacterCard(name="同名", description="卡"))
    repos.memory.add(_memory(character_id="同名", content="记忆"))
    repos.sessions.create("同名")

    assert (repos.novels.root / "同名.json").is_file()
    assert (repos.cards.root / "同名.json").is_file()
    assert (repos.memory.root / "同名.jsonl").is_file()
    assert (repos.sessions.root / "同名.jsonl").is_file()

    assert repos.novels.load("同名") is not None
    assert repos.cards.load("同名") is not None


def test_no_direct_file_access_in_business_code() -> None:
    """结构红线 ① 的静态检查：业务代码里不许出现直接的 data/ 路径读写。

    这条检查故意写得保守（只扫明显的 `Path("data/...")` / `open("data/..."`
    写法），避免误伤存储层自己。
    """
    import re
    from pathlib import Path as _Path

    src = _Path(__file__).resolve().parents[1] / "src" / "booksoul"
    offenders: list[str] = []
    pattern = re.compile(r"""(?:Path|open)\s*\(\s*[fr]?["'][^"']*data/""")

    for path in src.rglob("*.py"):
        if path.parent.name == "storage":
            continue  # 存储层就是干这个的
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(src)}:{number}")

    assert offenders == [], f"业务代码里出现直接文件访问：{offenders}"


def test_storage_modules_import_cleanly() -> None:
    """存储层不该反向依赖解析层（避免迁库时两层绑死）。"""
    import booksoul.storage as storage

    assert not hasattr(storage, "Chapter")
    assert hasattr(storage, "NovelRecord")
