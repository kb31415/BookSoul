"""存储抽象：Repository 接口 + 文件实现（`MVP_PLAN.md` §1「迭代友好性约束」）。

**为什么需要这一层**：`PROJECT_DESIGN.md` §13.6 写明第二阶段要从文件迁到
Postgres + pgvector。如果业务代码直接 `open("data/cards/xxx.json")`，迁库时要把
散落在 parser / identify / extract / memory / CLI 里的路径操作全找出来改，
改漏一个就是 bug；有了这一层，**只换实现，业务代码一行不动**。

四类数据各一个窄接口（`HANDOFF.md` §2 结构红线 ①）：

| 数据 | 路径 | 接口 | 文件实现 |
|---|---|---|---|
| 小说解析产物 | `data/novels/{book_id}.json` | `NovelRepository` | `FileNovelRepository` |
| 角色卡 | `data/cards/{character_id}.json` | `CardRepository` | `FileCardRepository` |
| 记忆 | `data/memory/{character_id}.jsonl` | `MemoryRepository` | `FileMemoryRepository` |
| 会话 | `data/sessions/{session_id}.jsonl` | `SessionRepository` | `FileSessionRepository` |

约定：

- **业务代码只依赖 Protocol**，构造时注入具体实现（阶段 8 的 CLI 负责装配）。
- 读取一律**宽容**：文件不存在返回 `None`/空列表，坏行跳过 —— 不阻塞主流程。
- 写入一律**显式**：需要目录就自动建，失败就抛 —— 不静默吞掉。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from booksoul.config import Settings, load_settings
from booksoul.schema import CharacterCard
from booksoul.storage.models import (
    ChapterNameCache,
    MemoryEntry,
    NovelRecord,
    SessionLog,
    utc_now_iso,
)

__all__ = [
    "CardRepository",
    "FileCardRepository",
    "FileMemoryRepository",
    "FileNovelRepository",
    "FileSessionRepository",
    "MemoryRepository",
    "NovelRepository",
    "RepositorySet",
    "SessionRepository",
    "build_repositories",
]


# ══════════════════════════ 工具 ══════════════════════════


def _read_json(path: Path) -> Any | None:
    """读 JSON；文件不存在或内容坏了都返回 `None`（宽容读取）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """逐行读 JSONL，跳过空行与坏行（宽容读取）。"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行跳过，不让一行脏数据废掉整个文件
                if isinstance(item, dict):
                    yield item
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return


def _write_jsonl(path: Path, items: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _safe_id(value: str) -> str:
    """把 id 收敛成安全文件名（防 `../` 之类的路径穿越）。"""
    cleaned = "".join(ch for ch in value if ch not in '\\/:*?"<>|').strip().strip(".")
    if not cleaned:
        raise ValueError(f"非法的 id：{value!r}")
    return cleaned


def content_id(*parts: str) -> str:
    """由内容派生稳定 id（用于记忆去重：同样内容不会重复入库）。"""
    digest = hashlib.sha1("\u241f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"mem_{digest}"


# ══════════════════════════ 接口 ══════════════════════════


@runtime_checkable
class NovelRepository(Protocol):
    """小说解析产物（阶段 2 产出，阶段 3 消费）。"""

    def save(self, novel: NovelRecord) -> str: ...
    def load(self, book_id: str) -> NovelRecord | None: ...
    def exists(self, book_id: str) -> bool: ...
    def list_ids(self) -> list[str]: ...
    def delete(self, book_id: str) -> bool: ...


@runtime_checkable
class CardRepository(Protocol):
    """角色卡（阶段 4–5 产出，阶段 6 消费）+ 按章的中间产物缓存（阶段 3）。"""

    def save(self, card: CharacterCard, character_id: str | None = None) -> str: ...
    def load(self, character_id: str) -> CharacterCard | None: ...
    def exists(self, character_id: str) -> bool: ...
    def list_ids(self) -> list[str]: ...
    def delete(self, character_id: str) -> bool: ...

    # ── 阶段 3：按章中间产物（`data/cards/{book_id}/chapters/{i}.names.json`）──

    def character_dir(self, book_id: str) -> Path: ...
    def save_chapter_names(self, book_id: str, cache: ChapterNameCache) -> Path: ...
    def load_chapter_names(self, book_id: str, chapter_index: int) -> ChapterNameCache | None: ...
    def load_all_chapter_names(self, book_id: str) -> dict[int, ChapterNameCache]: ...
    def save_json(self, book_id: str, filename: str, payload: Any) -> Path: ...
    def load_json(self, book_id: str, filename: str) -> Any | None: ...


@runtime_checkable
class MemoryRepository(Protocol):
    """记忆条目（阶段 7）。**条目结构，不是裸 messages 数组。**"""

    def add(self, entry: MemoryEntry) -> str: ...
    def list_by_character(self, character_id: str) -> list[MemoryEntry]: ...
    def touch(self, character_id: str, memory_id: str) -> MemoryEntry | None: ...
    def character_ids(self) -> list[str]: ...
    def clear(self, character_id: str) -> int: ...


@runtime_checkable
class SessionRepository(Protocol):
    """会话（阶段 6）。带预留的 `state`。"""

    def create(self, session_id: str, character_id: str = "") -> SessionLog: ...
    def load(self, session_id: str) -> SessionLog | None: ...
    def append_message(
        self, session_id: str, message: dict[str, Any], state: dict[str, Any] | None = None
    ) -> SessionLog: ...
    def update_state(self, session_id: str, state: dict[str, Any]) -> SessionLog | None: ...
    def list_ids(self) -> list[str]: ...
    def delete(self, session_id: str) -> bool: ...


# ══════════════════════════ 文件实现 ══════════════════════════


class FileNovelRepository:
    """`data/novels/{book_id}.json`。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, book_id: str) -> Path:
        return self.root / f"{_safe_id(book_id)}.json"

    def save(self, novel: NovelRecord) -> str:
        _write_json(self._path(novel.book_id), novel.model_dump(mode="json"))
        return novel.book_id

    def load(self, book_id: str) -> NovelRecord | None:
        raw = _read_json(self._path(book_id))
        if not isinstance(raw, dict):
            return None
        return NovelRecord.model_validate(raw)

    def exists(self, book_id: str) -> bool:
        return self._path(book_id).is_file()

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob("*.json") if path.is_file())

    def delete(self, book_id: str) -> bool:
        path = self._path(book_id)
        if not path.is_file():
            return False
        path.unlink()
        return True


class FileCardRepository:
    """`data/cards/{character_id}.json`（`PROJECT_DESIGN.md` §13.6）。

    另外管 `data/cards/{book_id}/` 下的按章中间产物 —— 这是阶段 3 的落盘位置
    （`PROMPT_DESIGN.md` §3：`{book_id}/chapters/{i}.names.json`）。
    路径拼接全部收敛在这里，业务代码只拿返回值。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, character_id: str) -> Path:
        return self.root / f"{_safe_id(character_id)}.json"

    @staticmethod
    def _resolve_id(card: CharacterCard, character_id: str | None) -> str:
        """没给 id 就用角色名（`PROJECT_DESIGN.md` §6 的 `name` 是必填）。"""
        resolved = character_id or card.name
        if not resolved:
            raise ValueError("角色卡既没给 character_id，name 也是空的")
        return resolved

    def save(self, card: CharacterCard, character_id: str | None = None) -> str:
        resolved = self._resolve_id(card, character_id)
        _write_json(self._path(resolved), card.model_dump(mode="json"))
        return resolved

    def load(self, character_id: str) -> CharacterCard | None:
        raw = _read_json(self._path(character_id))
        if not isinstance(raw, dict):
            return None
        return CharacterCard.model_validate(raw)

    def exists(self, character_id: str) -> bool:
        return self._path(character_id).is_file()

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob("*.json") if path.is_file())

    def delete(self, character_id: str) -> bool:
        path = self._path(character_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    # ── 阶段 5 会把 v2 导出版本也落盘，顺手提供 ──

    def save_tavern_v2(self, card: CharacterCard, character_id: str | None = None) -> str:
        resolved = self._resolve_id(card, character_id)
        target = self.root / f"{_safe_id(resolved)}.v2.json"
        _write_json(target, card.to_tavern_v2())
        return target.stem

    def load_tavern_v2(self, character_id: str) -> CharacterCard | None:
        raw = _read_json(self.root / f"{_safe_id(character_id)}.v2.json")
        if not isinstance(raw, dict):
            return None
        return CharacterCard.from_tavern(raw)

    # ── 阶段 3：按章中间产物 ──

    def character_dir(self, book_id: str) -> Path:
        """某本书的产物目录：`data/cards/{book_id}/`。"""
        return self.root / _safe_id(book_id)

    def _chapter_path(self, book_id: str, chapter_index: int) -> Path:
        return self.character_dir(book_id) / "chapters" / f"{chapter_index}.names.json"

    def save_chapter_names(self, book_id: str, cache: ChapterNameCache) -> Path:
        target = self._chapter_path(book_id, cache.chapter_index)
        _write_json(target, cache.model_dump(mode="json"))
        return target

    def load_chapter_names(self, book_id: str, chapter_index: int) -> ChapterNameCache | None:
        raw = _read_json(self._chapter_path(book_id, chapter_index))
        if not isinstance(raw, dict):
            return None
        return ChapterNameCache.model_validate(raw)

    def load_all_chapter_names(self, book_id: str) -> dict[int, ChapterNameCache]:
        """读回一本书全部已缓存的章节结果（章节按 index 升序）。"""
        chapters_dir = self.character_dir(book_id) / "chapters"
        if not chapters_dir.is_dir():
            return {}

        cached: dict[int, ChapterNameCache] = {}
        for path in chapters_dir.glob("*.names.json"):
            raw = _read_json(path)
            if not isinstance(raw, dict):
                continue
            try:
                cache = ChapterNameCache.model_validate(raw)
            except Exception:  # noqa: BLE001 - 坏缓存跳过，让它重跑
                continue
            cached[cache.chapter_index] = cache
        return dict(sorted(cached.items()))

    def save_json(self, book_id: str, filename: str, payload: Any) -> Path:
        """在 `data/cards/{book_id}/` 下落一个 JSON（如 `candidates.json`）。"""
        filename = _safe_id(filename) + (".json" if not filename.endswith(".json") else "")
        target = self.character_dir(book_id) / filename
        _write_json(target, payload)
        return target

    def load_json(self, book_id: str, filename: str) -> Any | None:
        filename = _safe_id(filename) + (".json" if not filename.endswith(".json") else "")
        return _read_json(self.character_dir(book_id) / filename)


class FileMemoryRepository:
    """`data/memory/{character_id}.jsonl`，append-only，每行一条（设计 §13.6）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, character_id: str) -> Path:
        return self.root / f"{_safe_id(character_id)}.jsonl"

    def add(self, entry: MemoryEntry) -> str:
        """写入一条记忆；**同内容不重复入库**（返回既有 id）。"""
        if not entry.character_id:
            raise ValueError("MemoryEntry.character_id 不能为空")

        resolved = entry.id or content_id(entry.character_id, entry.content, entry.source)
        payload = entry.model_copy(update={"id": resolved})
        entries = self.list_by_character(entry.character_id)

        for index, existing in enumerate(entries):
            if existing.id != resolved:
                continue
            if existing.content == payload.content:
                return resolved  # 已经有同一条，不重复追加
            # 同 id 不同内容：原地替换（人工修正场景）
            entries[index] = payload
            _write_jsonl(
                self._path(entry.character_id), (e.model_dump(mode="json") for e in entries)
            )
            return resolved

        _append_jsonl(self._path(entry.character_id), payload.model_dump(mode="json"))
        return resolved

    def list_by_character(self, character_id: str) -> list[MemoryEntry]:
        return [
            MemoryEntry.model_validate(raw) for raw in _iter_jsonl(self._path(character_id))
        ]

    def touch(self, character_id: str, memory_id: str) -> MemoryEntry | None:
        """命中一次：`hits += 1` 落盘（后续晋升/淘汰契约要用）。"""
        entries = self.list_by_character(character_id)
        for index, entry in enumerate(entries):
            if entry.id != memory_id:
                continue
            updated = entry.touch()
            entries[index] = updated
            _write_jsonl(self._path(character_id), (e.model_dump(mode="json") for e in entries))
            return updated
        return None

    def character_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob("*.jsonl") if path.is_file())

    def clear(self, character_id: str) -> int:
        entries = self.list_by_character(character_id)
        path = self._path(character_id)
        if path.is_file():
            path.unlink()
        return len(entries)


class FileSessionRepository:
    """`data/sessions/{session_id}.jsonl`，append-only（设计 §13.6）。

    行格式：

    - 第一行 `{"kind": "meta", "id", "character_id", "created_at", "updated_at", "state"}`
    - 之后每行 `{"kind": "message", "message": {...}}`

    追加消息是 **O(1) 真 append**；改 `state` 时重写文件（会话量小，可接受）。
    """

    _KIND_META = "meta"
    _KIND_MESSAGE = "message"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{_safe_id(session_id)}.jsonl"

    def _read(self, session_id: str) -> SessionLog | None:
        meta: dict[str, Any] | None = None
        messages: list[dict[str, Any]] = []

        for raw in _iter_jsonl(self._path(session_id)):
            kind = raw.get("kind")
            if kind == self._KIND_META:
                meta = raw
            elif kind == self._KIND_MESSAGE:
                message = raw.get("message")
                if isinstance(message, dict):
                    messages.append(message)

        if meta is None:
            return None
        meta = {k: v for k, v in meta.items() if k != "kind"}
        return SessionLog.model_validate({**meta, "messages": messages})

    def _write(self, session: SessionLog) -> None:
        rows: list[dict[str, Any]] = [
            {"kind": self._KIND_META, **session.model_dump(mode="json", exclude={"messages"})}
        ]
        rows.extend(
            {"kind": self._KIND_MESSAGE, "message": message} for message in session.messages
        )
        _write_jsonl(self._path(session.id), rows)

    def create(self, session_id: str, character_id: str = "") -> SessionLog:
        session = SessionLog(id=session_id, character_id=character_id)
        self._write(session)
        return session

    def load(self, session_id: str) -> SessionLog | None:
        return self._read(session_id)

    def append_message(
        self, session_id: str, message: dict[str, Any], state: dict[str, Any] | None = None
    ) -> SessionLog:
        """追加一条消息；给了 `state` 就顺带更新预留状态。"""
        if not isinstance(message, dict):
            raise TypeError(f"message 必须是 dict，收到 {type(message).__name__}")

        current = self._read(session_id)
        if current is None:
            current = SessionLog(id=session_id, character_id=str(message.get("character_id", "")))

        current = current.model_copy(
            update={
                "messages": [*current.messages, message],
                "updated_at": utc_now_iso(),
                "state": state if state is not None else current.state,
            }
        )
        self._write(current)
        return current

    def update_state(self, session_id: str, state: dict[str, Any]) -> SessionLog | None:
        current = self._read(session_id)
        if current is None:
            return None
        updated = current.model_copy(update={"state": dict(state), "updated_at": utc_now_iso()})
        self._write(updated)
        return updated

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob("*.jsonl") if path.is_file())

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        return True


# ══════════════════════════ 装配 ══════════════════════════


class RepositorySet:
    """四个 Repository 的打包，便于一次性注入（阶段 8 的 CLI 用它装配）。

    `repository_set.load()` 是业务代码唯一需要认识的东西 ——
    换存储后端时只需要换这里的构造，上层不动。
    """

    def __init__(
        self,
        novels: NovelRepository,
        cards: CardRepository,
        memory: MemoryRepository,
        sessions: SessionRepository,
    ) -> None:
        self.novels = novels
        self.cards = cards
        self.memory = memory
        self.sessions = sessions

    def ensure_dirs(self) -> "RepositorySet":
        for repository in (self.novels, self.cards, self.memory, self.sessions):
            root = getattr(repository, "root", None)
            if root is not None:
                Path(root).mkdir(parents=True, exist_ok=True)
        return self


def build_repositories(settings: Settings | None = None) -> RepositorySet:
    """按配置装配文件版 Repository（MVP 的唯一实现）。"""
    current = (settings or load_settings()).ensure_dirs()
    return RepositorySet(
        novels=FileNovelRepository(current.novels_dir),
        cards=FileCardRepository(current.cards_dir),
        memory=FileMemoryRepository(current.memory_dir),
        sessions=FileSessionRepository(current.sessions_dir),
    )
