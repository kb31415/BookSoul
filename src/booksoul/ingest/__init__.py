"""文本摄取层（阶段 2）。

`parser.py`：编码探测（utf-8 / gbk / gb18030）、清洗、分章（含按长度兜底切分）。
**纯确定性代码，不含任何 LLM 调用。**
"""

from booksoul.ingest.parser import (
    CHAPTER_TITLE_MAX_LENGTH,
    DEFAULT_ENCODINGS,
    DEFAULT_HARD_SPLIT_LENGTH,
    DEFAULT_MAX_CHAPTER_LENGTH,
    Chapter,
    Novel,
    book_id_from_path,
    clean_text,
    decode_bytes,
    detect_encoding,
    dump_novel,
    find_chapter_title,
    from_novel_record,
    is_ad_line,
    parse_file,
    parse_text,
    split_long_chapters,
    split_text,
    to_novel_record,
)

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
