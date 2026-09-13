"""TXT 解析与分章（`MVP_PLAN.md` 阶段 2）。

目标：把 `.txt` 变成结构化的章节列表 —— 确定性代码，**不含任何 LLM 调用**。

三件事：

1. **编码探测**：中文小说编码混杂（utf-8 / gbk / gb18030），先探测再解码。
   顺序是「先严后宽」：utf-8 严格解码最不容易误判；gb18030 是 gbk 的超集
   （编码探测用宽的那层，输出时报告实际命中的窄名字）。
2. **分章**：正则匹配 `第X章 / 节 / 回` 等标题行；**找不到标题就按长度切**
   （`MVP_PLAN.md` 阶段 2「常见坑」明确要求的兜底，不能因此阻塞主流程）。
3. **清洗**：统一换行、去常见的盗版站广告行、压缩连续空行。

下游约束（`PROMPT_DESIGN.md` §3）：阶段 3 要把「章节标题 + 章节正文」喂给 LLM，
且**建议单章不超过 6000 字**。因此本模块提供 `split_long_chapters()`，
把超长章节在**段落边界**再切成若干块 —— 这是可选项，默认不开启。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

# 只依赖存储层的**模型**（models.py 不反向依赖本模块，无循环）；
# Repository 接口仅在类型标注里用到，所以放 TYPE_CHECKING。
from booksoul.storage.models import NovelRecord

if TYPE_CHECKING:
    from booksoul.storage import NovelRepository

__all__ = [
    "CHAPTER_TITLE_MAX_LENGTH",
    "DEFAULT_ENCODINGS",
    "DEFAULT_HARD_SPLIT_LENGTH",
    "DEFAULT_MAX_CHAPTER_LENGTH",
    "Chapter",
    "Novel",
    "book_id_from_path",
    "clean_text",
    "decode_bytes",
    "detect_encoding",
    "dump_novel",
    "find_chapter_title",
    "from_novel_record",
    "is_ad_line",
    "parse_file",
    "parse_text",
    "split_long_chapters",
    "split_text",
    "to_novel_record",
]

#: 编码探测顺序：先严后宽。utf-8 严格解码最不容易误判；gb18030 是 gbk 的超集，
#: 放在 gbk 前面（探测用宽的那层），命中后按 gbk 窄名报告。
DEFAULT_ENCODINGS: tuple[str, ...] = ("utf-8", "gb18030", "big5")

#: 没有章节标题时的兜底切分长度（字符数，1 汉字算 1）。
DEFAULT_HARD_SPLIT_LENGTH = 3000

#: 单章超过这个长度建议再切（`PROMPT_DESIGN.md` §3：建议不超过 6000 字）。
DEFAULT_MAX_CHAPTER_LENGTH = 6000

#: 标题行的最大长度 —— 超过就不是标题而是"正文里恰好出现'第X章'"。
CHAPTER_TITLE_MAX_LENGTH: int = 40

# ── 章节标题：`第X章/节/回/篇/卷/集/幕` ──
_CN_NUM = r"0-9０-９一二三四五六七八九十百千万零两"
_CHAPTER_PATTERN = re.compile(
    rf"^第\s*[{_CN_NUM}]+\s*[章节回篇卷集幕]"
    rf"(?:[ \t　]*[:：、.．·]?[ \t　]*)(?P<title>.{{0,{CHAPTER_TITLE_MAX_LENGTH}}})$"
)

# ── 没有"第X章"的常见标题：序章 / 楔子 / 尾声 / 番外 / 后记 …… ──
_SPECIAL_TITLES: tuple[str, ...] = (
    "序章", "序言", "引子", "楔子", "前言", "后记", "尾声", "终章",
    "番外", "外传", "附录", "设定",
)
#: 特殊标题后面允许的最大后缀（"番外 之后的事" = 5，够用）。
#: 超过它且第一字是汉字 → 判定为正文句子（"序章的写法他改了三遍。"）。
_SPECIAL_SUFFIX_MAX: int = 6
_SPECIAL_PATTERN = re.compile(
    rf"^(?P<title>{'|'.join(_SPECIAL_TITLES)})(?![章节回篇卷集幕])"
    rf"[ \t　]*[:：、.．·]?[ \t　]*(?P<suffix>.{{0,{CHAPTER_TITLE_MAX_LENGTH}}})$"
)
#: 这些词后面若紧跟汉字，多半是句子而不是标题（"最后一章里他死了。"是正文说法，
#: 而整行只有"最后一章"时它是真标题）。
_CHAPTER_FALSE_FRIENDS: tuple[str, ...] = ("最后", "下一", "上一", "这一", "第一时")
_CHAPTER_FALSE_FRIEND_RE = re.compile(
    rf"^(?:{'|'.join(_CHAPTER_FALSE_FRIENDS)})(?=[\u4e00-\u9fff])"
)
#: 标题后面不该紧跟的汉字（"第一章节"）
_NOT_A_TITLE = re.compile(r"^[章节回篇卷集幕]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
#: 标题里不该出现的**句末**标点 —— "第一章正文。" / "序章正文。" 是正文句子，
#: 不是标题（真实章节标题不带句号）。注意**不含**半角句点 `.`：它是合法的
#: 标题分隔符（"第一章.雨夜" 要认）。
_TITLE_FORBIDDEN_PUNCT = re.compile(r"[。！？；!?;]")

# ── 章节分隔线（第二套分章判据）──
#
# 实测：《咎由自取》（31 万字，616KB）**没有「第X章」**，只有 `----` 分隔线；
# 章节标题是**分隔线上一行那个短句**（"下水道的老鼠" / "有病" / "不小心" …）。
# 这类 txt 在中文网文里很常见（作者用分隔线替代章节标题）。
#
# 没有这套判据时，兜底逻辑会把整本主文当成一块"卷首"再按字数硬切，
# 切点与真实章节边界完全无关 —— 比崩溃更隐蔽（章节内容会被劈开又混在一起）。
_SEPARATOR_RE = re.compile(r"^[\-\u2014\u2013_=]{4,}$")

#: 分隔线上一行当标题时的最大长度 —— 超过就是正文句子，不是标题。
SEPARATOR_TITLE_MAX_LENGTH: int = 24

#: 首个分隔线之前的"卷首"（书名 / 简介 / 文案）短于这个值就丢弃。
_SEPARATOR_HEAD_MIN: int = 120

# ── 清洗：盗版站广告行 ──
# 原则：**只做确定性清洗，不猜语义**。误删正文比留噪声严重得多，所以
# ① 明确的书站黑话用 `search`（这些词在正常叙事里几乎不出现）；
# ② 常见词（书签 / 月票 / 手机 / 域名 / 目录 / 本章完）一律**用行首标记锚定**，
#    避免干掉"他把书签夹回了第一百页"这种正文。
# ③ 像"月票"这种既能当广告词又能当宾语的名词，还要求**动词前缀**或**整行只有它**，
#    否则"月票这两个字，他只在信里写过一次。"会被误删。
_LEADING = r"^[ \t　]*"          # 允许行首缩进
_LINE_END = r"[ \t　]*$"          # 该行到此结束

_AD_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ① 书站黑话：正常叙事里不会出现的组合
    re.compile(r"记住本站|本站域名|请记住|收藏本站|笔趣阁|顶点小说|全本小说|无弹窗"),
    re.compile(r"txt下载|电子书下载|免费阅读|全本阅读|最新章节|章节目录|全文阅读|在线阅读"),
    re.compile(r"未完待续"),
    # ② 行首标记词（"加入书签"式），不命中把"书签"当宾语说的正文
    re.compile(_LEADING + r"(?:加入|添加|收藏)?书签"),
    re.compile(_LEADING + r"手机(?:用户|版|阅读|访问|请)"),
    # 导航词：**整行**才算（"上一章写到的那把剑"是正文，不能删）
    re.compile(_LEADING + r"(?:上一章|下一章|返回目录|点击下一页|翻页|本章完)" + _LINE_END),
    # ③ 名词型：要么带动词前缀，要么整行就是这个词（可带括号注释）
    re.compile(_LEADING + r"(?:求|投|要|砸)\s*月票"),
    re.compile(_LEADING + r"月票" + _LINE_END),
    re.compile(_LEADING + r"(?:求|投)\s*推荐票"),
    re.compile(_LEADING + r"推荐票" + _LINE_END),
    re.compile(_LEADING + r"求[ \t　]*(?:收藏|订阅|追更|打赏|点赞)" + _LINE_END),
    re.compile(_LEADING + r"(?:订阅|追更|打赏)[ \t　]*(?:一下|下|吧|呗)?" + _LINE_END),
    # ④ 整行括号注释（"（本章完）"），不碰"本章完结的时候……"
    re.compile(_LEADING + r"[（(【\[]\s*(?:本章完|未完待续|全文完|求月票|求推荐票)\s*[)）】\]][ \t　]*$"),
)

#: 独立成行的 URL。
_URL_RE = re.compile(r"^[ \t　]*(?:https?://|www\.)\S+[ \t　]*$", re.IGNORECASE)

#: 三个及以上连续换行 → 压成一个空行。
_BLANK_LINES_RE = re.compile(r"\n{3,}")

#: 这些标点适合当"在段落边界断开"的位置（避免把句子劈开）。
_SENTENCE_END = "。！？!?…”』」\n"


# ────────────────────────── 数据契约 ──────────────────────────


class Chapter(BaseModel):
    """一章：`{index, title, content}`（`MVP_PLAN.md` 阶段 2 任务 2）。

    `index` 从 0 开始，对应 `PROMPT_DESIGN.md` 里 `data/cards/{book_id}/chapters/{i}.names.json`
    的 `{i}`。
    """

    model_config = ConfigDict(extra="ignore")

    index: int
    title: str
    content: str


class Novel(BaseModel):
    """一本书的解析结果（落盘为 `data/novels/{book_id}.json`）。"""

    model_config = ConfigDict(extra="ignore")

    book_id: str
    source_path: str | None = None
    encoding: str = "utf-8"
    char_count: int = 0
    chapters: list[Chapter] = Field(default_factory=list)

    def split_long(self, max_length: int = DEFAULT_MAX_CHAPTER_LENGTH) -> Novel:
        """返回一个新的 `Novel`，把超长章节按段落边界再切（不修改自身）。"""
        return self.model_copy(
            update={"chapters": split_long_chapters(self.chapters, max_length=max_length)}
        )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Novel:
        return cls.model_validate(raw)


# ────────────────────────── 编码探测 ──────────────────────────


def _bom_encoding(sample: bytes) -> tuple[str, int] | None:
    """BOM 是唯一确凿的线索，优先判它。返回 `(编码, BOM 字节数)`。"""
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", 3
    if sample.startswith(b"\xff\xfe"):
        return "utf-16", 2
    if sample.startswith(b"\xfe\xff"):
        return "utf-16", 2
    return None


def _looks_like_utf16(sample: bytes) -> bool:
    """无 BOM 的 UTF-16：ASCII 文本里每隔一字节就是 0x00。

    只作为兜底线索 —— 正常的中文小说不会命中。
    """
    if len(sample) < 16:
        return False
    even_nulls = sum(1 for i in range(0, min(len(sample), 512), 2) if sample[i] == 0)
    odd_nulls = sum(1 for i in range(1, min(len(sample), 512), 2) if sample[i] == 0)
    pairs = max(1, min(len(sample), 512) // 2)
    return even_nulls / pairs > 0.8 or odd_nulls / pairs > 0.8


def detect_encoding(data: bytes, candidates: Iterable[str] = DEFAULT_ENCODINGS) -> str:
    """探测字节串的编码，返回可用于 `bytes.decode()` 的名字。

    策略：**先 BOM，再严格解码，先严后宽**。

    - BOM 命中 → 直接用它（`utf-8-sig` 会把 BOM 一起吃掉）。
    - 否则按 `utf-8` → `gb18030` → `big5` 顺序**严格解码**：只要有一段解不开就
      换下一个。因为 utf-8 对 GBK 文本几乎必然报错，而 gb18030 能很宽松地
      解出 utf-8 字节，所以 utf-8 必须排在前面。
    - 全都不行 → 用 `gb18030` 尽力解（`MVP_PLAN.md`「中文编码混杂」的兜底，
      解码时用 `errors="replace"`，不阻塞主流程）。
    """
    if not data:
        return "utf-8"

    bom = _bom_encoding(data)
    if bom is not None:
        return bom[0]

    if _looks_like_utf16(data):
        return "utf-16"

    for encoding in candidates:
        try:
            data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return encoding
    return "gb18030"


def decode_bytes(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    """解码字节串，返回 `(文本, 实际编码)`。

    不给 `encoding` 就走 `detect_encoding()`。解码失败时**不抛异常**：退回
    `gb18030` + `errors="replace"`，保证主流程能继续（坏字符变成 ``）。
    """
    resolved = encoding or detect_encoding(data)
    bom = _bom_encoding(data)
    if bom is not None and resolved == bom[0]:
        data = data[bom[1] :]  # utf-16 由 Python 自己处理 BOM，只手动剥 utf-8-sig

    try:
        return data.decode(resolved), resolved
    except (UnicodeDecodeError, LookupError):
        return data.decode("gb18030", errors="replace"), "gb18030"


# ────────────────────────── 清洗 ──────────────────────────


def is_ad_line(line: str) -> bool:
    """判断一行是不是盗版站广告 / 导航行（纯确定性规则，不猜语义）。"""
    stripped = line.strip()
    if not stripped:
        return False
    if _URL_RE.match(stripped):
        return True
    return any(pattern.search(stripped) for pattern in _AD_PATTERNS)


def clean_text(text: str) -> str:
    """统一换行 → 去掉空白行与广告行 → 压缩连续空行 → 整体 strip。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    kept: list[str] = []
    for line in normalized.split("\n"):
        line = line.rstrip()
        if not line.strip():
            # 空行保留一个，用于后续段落切分（"去空行"指去掉空白垃圾行）
            kept.append("")
            continue
        if is_ad_line(line):
            continue
        kept.append(line)

    cleaned = _BLANK_LINES_RE.sub("\n\n", "\n".join(kept))
    return cleaned.strip()


# ────────────────────────── 分章 ──────────────────────────


def find_chapter_title(line: str) -> str | None:
    """这一行是章节标题吗？是就返回标题，否则 `None`。

    认两种：① 有编号的 `第X章/节/回/篇/卷/集/幕`；② 无编号的
    `序章 / 楔子 / 尾声 / 番外` 等。标题行超过 `CHAPTER_TITLE_MAX_LENGTH`
    一律不算 —— 那种多半是正文里恰好写到了"第一章"。
    """
    stripped = line.strip()
    if not stripped or len(stripped) > CHAPTER_TITLE_MAX_LENGTH + 10:
        return None

    match = _CHAPTER_PATTERN.match(stripped)
    if match is not None:
        tail = stripped[match.start("title") :]
        # "第一章节的写法……" 里 "第一章" 后面紧跟"节" —— 那是正文，不是标题
        if _NOT_A_TITLE.match(tail):
            return None
        # "最后一章里他死了。" / "下一章见。" —— 以虚词开头又紧跟汉字的，是句子
        if _CHAPTER_FALSE_FRIEND_RE.match(tail):
            return None
        # "第一章正文。" —— 标题部分带句末标点，是正文句子（真实标题不带句号）
        if _TITLE_FORBIDDEN_PUNCT.search(tail):
            return None
        return stripped

    match = _SPECIAL_PATTERN.match(stripped)
    if match is not None:
        suffix = match.group("suffix") or ""
        # "序章的写法他改了三遍。" —— 紧跟汉字、后缀又太长，那是句子不是标题；
        # 而 "番外 之后的事"（后缀 5 字）这种真标题要留下。
        if _CJK.match(suffix) and len(suffix) > _SPECIAL_SUFFIX_MAX:
            return None
        # "序章正文。" —— 同理：带句末标点的是正文句子
        if _TITLE_FORBIDDEN_PUNCT.search(suffix):
            return None
        return stripped
    return None


def _scan_titles(text: str) -> list[tuple[str, str, int]]:
    """逐行扫描，返回 `[(标题, 标题行原文, 内容起点)]`。

    内容起点是**标题行之后**的位置（标题不进 `content`）。
    每章的终点由"下一章的标题行起点"决定，见 `_split_into_chapters`。
    """
    found: list[tuple[str, str, int]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        title = find_chapter_title(line)
        if title is not None:
            found.append((title, line, offset + len(raw_line)))
        offset += len(raw_line)
    return found


def _find_split_point(text: str, start: int, limit: int) -> int:
    """在 `[start, start+limit]` 内找一个尽量靠后的"句子/段落"边界。"""
    window = text[start : start + limit]
    if len(window) < limit:
        return len(text)

    best = -1
    for index, char in enumerate(window):
        if char in _SENTENCE_END:
            best = index + 1
    if best <= 0:
        return start + limit
    return start + best


def _chunk_text(text: str, length: int) -> list[str]:
    """按长度在句子边界切块，返回非空的正文块。"""
    if length <= 0:
        raise ValueError("length 必须为正整数")

    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = _find_split_point(text, cursor, length)
        content = text[cursor:end].strip()
        cursor = end
        if content:
            chunks.append(content)
    return chunks


def split_text(
    text: str,
    length: int = DEFAULT_HARD_SPLIT_LENGTH,
    title_prefix: str = "第",
) -> list[Chapter]:
    """兜底切分：没有章节标题时，按长度在句子边界切。

    `MVP_PLAN.md` 阶段 2 明确要求这个兜底（"找不到标题就按长度切"），
    风险预案里也写了"章节切分失败不阻塞主流程"。
    """
    suffix = "部分" if title_prefix else ""
    return [
        Chapter(index=index, title=f"{title_prefix}{index + 1}{suffix}", content=chunk)
        for index, chunk in enumerate(_chunk_text(text, length))
    ]


def _split_by_separators(text: str) -> list[Chapter]:
    """第二套分章判据：用 `----` 分隔线切。

    实测用例《咎由自取》：全篇没有「第X章」，作者用 `----` 分隔章节，
    **章节标题是分隔线上一行的短句**：

    ```
    下水道的老鼠
    --------------------------------------------------
    「哥哥，你不先给我看看？」
    ```

    切法（注意标题在分隔线**之前**、正文在**之后**）：

    ```
     título_i        ← 第 i 章的标题（分隔线上一行）
    ----            ← 第 i 个分隔线
     正文_i          ← 第 i 章的正文
     título_{i+1}    ← 第 i+1 章的标题
    ----
    ```

    所以：第 i 章正文 = `第 i 个分隔线之后` → `第 i+1 个标题行之前`。
    首个分隔线之前是书名/简介/文案，短于 `_SEPARATOR_HEAD_MIN` 就丢弃。

    没有分隔线时返回 `[]`（由调用方继续退到按长度切）。
    """
    lines = text.split("\n")
    separator_indexes = [
        index for index, line in enumerate(lines) if _SEPARATOR_RE.match(line.strip())
    ]
    if not separator_indexes:
        return []

    # ① 先把每个分隔线的"标题行下标"定位出来（往上找第一个非空行，且必须够短）
    def find_title_line(separator: int, lower_bound: int) -> int | None:
        for probe in range(separator - 1, lower_bound - 1, -1):
            candidate = lines[probe].strip()
            if not candidate:
                continue
            if len(candidate) <= SEPARATOR_TITLE_MAX_LENGTH:
                return probe
            return None
        return None

    titles: list[int | None] = []
    lower = 0
    for separator in separator_indexes:
        title_line = find_title_line(separator, lower)
        titles.append(title_line)
        # 下一个标题不会再跑到这一段里
        lower = separator + 1

    # ② 逐段取正文：[分隔线+1, 下一个标题行)
    chapters: list[Chapter] = []
    index = 0
    for position, separator in enumerate(separator_indexes):
        start = separator + 1
        if position + 1 < len(separator_indexes):
            next_title = titles[position + 1]
            end = next_title if next_title is not None else separator_indexes[position + 1]
        else:
            end = len(lines)

        content = "\n".join(lines[start:end]).strip()
        title_line = titles[position]
        title = lines[title_line].strip() if title_line is not None else ""

        # 第 0 段之前（首个分隔线之前）是书名/简介 —— 短则丢弃
        if position == 0:
            head = "\n".join(lines[: title_line if title_line is not None else separator]).strip()
            if len(head) >= _SEPARATOR_HEAD_MIN:
                chapters.append(Chapter(index=index, title="（卷首）", content=head))
                index += 1

        if content:
            chapters.append(
                Chapter(index=index, title=title or f"第{index + 1}节", content=content)
            )
            index += 1

    # ③ 最后一个分隔线之后可能还有内容（书末）
    tail_start = separator_indexes[-1] + 1
    tail = "\n".join(lines[tail_start:]).strip()
    if tail and (not chapters or tail != chapters[-1].content):
        first_line = tail.split("\n")[0].strip()
        label = first_line if len(first_line) <= SEPARATOR_TITLE_MAX_LENGTH else "（书末）"
        chapters.append(Chapter(index=index, title=label, content=tail))

    return chapters


def _split_into_chapters(text: str, fallback_length: int) -> list[Chapter]:
    spans = _scan_titles(text)
    by_separator = _split_by_separators(text)

    # 两套判据都可能命中，取**分辨率更高**的那套。
    #
    # 为什么要比较：《咎由自取》里两套都命中 —— 番外 15 篇有「番外N」标题（标题判据
    # 抓到 15 章），而 `----` 分隔线有 85 条（分隔线判据抓到 85 章）。若只认标题判据，
    # 24.8 万字主文会整体落进"第一个标题之前"被当成一块（卷首），再按字数硬切成
    # 42 段 —— 切点与真实章节边界无关，比崩溃更隐蔽。
    #
    # 反向也不能一刀切用分隔线：真有「第X章」的书里，正文偶尔也会有分隔线，
    # 此时标题判据才是权威（名字比"第N节"有意义）。所以按"谁能切出更多真实边界"选。
    if by_separator and len(by_separator) > 2 * len(spans):
        return by_separator

    if not spans:
        if by_separator:
            return by_separator
        return split_text(text, length=fallback_length)

    chapters: list[Chapter] = []
    index = 0

    # 第一个标题之前的内容：短（< 200 字）当封面/广告噪声丢弃，长则作为卷首。
    head_end = spans[0][2] - len(spans[0][1]) - 1
    head = text[: max(0, head_end)].strip()
    if len(head) >= 200:
        chapters.append(Chapter(index=index, title="（卷首）", content=head))
        index += 1

    for position, (title, line, content_start) in enumerate(spans):
        if position + 1 < len(spans):
            # 下一章标题行的起点 = 它的内容起点 - 标题行长度 - 换行符
            end = spans[position + 1][2] - len(spans[position + 1][1]) - 1
        else:
            end = len(text)
        content = text[content_start : max(content_start, end)].strip()
        chapters.append(Chapter(index=index, title=title, content=content))
        index += 1

    # 全部章节都空 → 退回按长度切，别产出"一个空章的列表"
    if not any(chapter.content for chapter in chapters):
        return split_text(text, length=fallback_length)
    return chapters


def split_long_chapters(
    chapters: list[Chapter], max_length: int = DEFAULT_MAX_CHAPTER_LENGTH
) -> list[Chapter]:
    """把超长章节按段落边界再切，`index` 与 `title` 重排（`PROMPT_DESIGN.md` §3）。"""
    if max_length <= 0:
        raise ValueError("max_length 必须为正整数")

    expanded: list[Chapter] = []
    for chapter in chapters:
        chunks = _chunk_text(chapter.content, max_length)
        if len(chunks) <= 1:
            expanded.append(chapter)
            continue
        total = len(chunks)
        for position, chunk in enumerate(chunks):
            expanded.append(
                Chapter(title=f"{chapter.title}（{position + 1}/{total}）", index=0, content=chunk)
            )

    # 重排 index，保证与 `data/cards/{book_id}/chapters/{i}.names.json` 的 {i} 对齐
    return [
        Chapter(index=index, title=chapter.title, content=chapter.content)
        for index, chapter in enumerate(expanded)
    ]


# ────────────────────────── 解析入口 ──────────────────────────


#: 文件名里常见的"书名装饰"符号 —— 它们不是书名的一部分，book_id 里要去掉。
_BOOK_ID_TRIM = "《》〈〉「」『』【】[]()（）\"'“”‘’ 　._-—·"

#: book_id 的最大长度（太长会把文件路径撑爆，也难读）。
_BOOK_ID_MAX_LENGTH: int = 60


def book_id_from_path(path: str | Path, content: str = "") -> str:
    """从文件名派生 `book_id`（阶段 3 起 `data/cards/{book_id}/` 用它）。

    规则：去掉扩展名 → 去掉书名号/引号这类装饰 → 把文件系统不友好字符换成 `_`。

    实测用例：`《咎由自取》.txt` 应该得到 `咎由自取`，
    而不是 `《咎由自取》`（书名号进目录名既难看又容易出问题）。

    如果清理后什么都不剩（文件名全是标点），退回用内容哈希，保证唯一。
    """
    stem = Path(path).stem
    trimmed = stem.strip(_BOOK_ID_TRIM)
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", trimmed).strip(_BOOK_ID_TRIM)
    slug = slug[:_BOOK_ID_MAX_LENGTH]
    if slug:
        return slug
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    return f"book_{digest}"


def parse_text(
    text: str,
    *,
    book_id: str = "",
    source_path: str | None = None,
    encoding: str = "utf-8",
    fallback_length: int = DEFAULT_HARD_SPLIT_LENGTH,
    max_chapter_length: int | None = None,
) -> Novel:
    """把已解码的全文解析成 `Novel`。

    `max_chapter_length` 给了就顺带把超长章节切开（阶段 3 需要）。
    """
    cleaned = clean_text(text)
    chapters = _split_into_chapters(cleaned, fallback_length=fallback_length)
    if max_chapter_length is not None:
        chapters = split_long_chapters(chapters, max_length=max_chapter_length)

    resolved_id = book_id or book_id_from_path(source_path or "book", cleaned)
    return Novel(
        book_id=resolved_id,
        source_path=source_path,
        encoding=encoding,
        char_count=len(cleaned),
        chapters=chapters,
    )


def parse_file(
    path: str | Path,
    *,
    encoding: str | None = None,
    fallback_length: int = DEFAULT_HARD_SPLIT_LENGTH,
    max_chapter_length: int | None = None,
) -> Novel:
    """读一个 `.txt`：探测编码 → 解码 → 清洗 → 分章。"""
    file_path = Path(path)
    data = file_path.read_bytes()
    text, resolved_encoding = decode_bytes(data, encoding)

    return parse_text(
        text,
        book_id=book_id_from_path(file_path, text),
        source_path=str(file_path),
        encoding=resolved_encoding,
        fallback_length=fallback_length,
        max_chapter_length=max_chapter_length,
    )


def to_novel_record(novel: Novel) -> NovelRecord:
    """`Novel`（解析层）→ `NovelRecord`（存储层）。

    刻意分成两层模型：存储层不解释解析结果的内部结构，将来换存储后端时
    不需要动解析层。
    """
    return NovelRecord(
        book_id=novel.book_id,
        source_path=novel.source_path,
        encoding=novel.encoding,
        char_count=novel.char_count,
        chapter_count=len(novel.chapters),
        payload=novel.model_dump(mode="json"),
    )


def from_novel_record(record: NovelRecord) -> Novel:
    """`NovelRecord`（存储层）→ `Novel`（解析层）。"""
    return Novel.model_validate(record.payload)


def dump_novel(novel: Novel, repository: NovelRepository) -> str:
    """把解析结果写进 `NovelRepository`，返回 `book_id`。

    **这里刻意不接收目录、也不自己 `open()`** —— 存储是 Repository 的职责
    （`HANDOFF.md` §2 结构红线 ①：业务代码里不允许直接文件操作）。
    调用方负责装配实现：

    ```python
    repos = build_repositories()          # 阶段 8 的 CLI 做这件事
    dump_novel(novel, repos.novels)
    ```
    """
    return repository.save(to_novel_record(novel))
