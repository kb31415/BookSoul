"""抓取阶段 3/4 的真实验收语料（公版中文小说）。

**为什么需要**：阶段 3/4 的验收标准（"前 5 名确实是主要角色"、"抽出的字段像书中人"）
没法用 mock 证明。但真实小说有版权风险，不能进 git —— 所以语料靠这个脚本按需生成。

语料来源：[Project Gutenberg](https://www.gutenberg.org) 的公版中文小说。
当前用 **《宛如约》**（Gutenberg #27185）：15 回、人名反复出现、结构干净，
前 5 回约 2.5 万字，跑一轮真实识别只要几分钱。

用法：

    python scripts/fetch_corpus.py              # 默认取前 5 回
    python scripts/fetch_corpus.py --chapters 8 # 多取几回（更贵）

产物（**都不入 git**，见 `.gitignore` 的 `data/`）：

    data/novels/宛如约.txt        UTF-8，前 N 回
    data/novels/宛如约_gbk.txt    同一内容的 GBK 版（验证编码探测）

之后：

    booksoul ingest data/novels/宛如约.txt
    booksoul identify 宛如约
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from booksoul.ingest import parse_text  # noqa: E402

GUTENBERG_ID = 27185
BOOK_TITLE = "宛如约"
URL = f"https://www.gutenberg.org/cache/epub/{GUTENBERG_ID}/pg{GUTENBERG_ID}.txt"
OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "novels"

START_MARKER = "*** START OF THE PROJECT GUTENBERG"
END_MARKER = "*** END OF THE PROJECT GUTENBERG"


def is_complete(data: bytes) -> bool:
    """完整性判据。

    走过两条弯路，记下来省得再踩：

    1. **不能要求文件以 `***` 结尾** —— Gutenberg 在 END 标记之后还有一段
       版权声明与换行，真正的结尾是普通英文句子。
    2. **必须能完整 UTF-8 解码** —— 中途断开时最典型的症状是最后一个多字节
       汉字被截断，`decode` 会在文件末尾附近报错。这条才是靠谱的判据。
    """
    if b"*** END OF THE PROJECT GUTENBERG" not in data:
        return False
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def download(attempts: int = 4) -> bytes:
    """下载并校验完整性；不完整就重试。"""
    last = b""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(URL, timeout=60) as response:
                data = response.read()
        except Exception as exc:  # noqa: BLE001
            print(f"  第 {attempt} 次请求失败：{exc}")
            continue

        if is_complete(data):
            print(f"  下载完成（第 {attempt} 次，{len(data)} 字节）")
            return data

        print(f"  第 {attempt} 次拿到不完整文件（{len(data)} 字节），重试……")
        last = data

    raise RuntimeError(f"重试 {attempts} 次仍未取得完整文件（最后一次 {len(last)} 字节）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"抓取《{BOOK_TITLE}》作为真实验收语料")
    parser.add_argument("--chapters", type=int, default=5, help="取前几回（默认 5）")
    args = parser.parse_args(argv)

    print(f"从 Project Gutenberg 抓取《{BOOK_TITLE}》(#{GUTENBERG_ID})……")
    data = download()
    text = data.decode("utf-8-sig")

    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    body = text[start:end] if start != -1 and end != -1 else text

    novel = parse_text(body, book_id=BOOK_TITLE)
    if not novel.chapters:
        print("解析不出任何章节 —— 分章规则可能不匹配这本书", file=sys.stderr)
        return 1

    kept = novel.chapters[: args.chapters]
    print(f"全书 {len(novel.chapters)} 回，取前 {len(kept)} 回：")
    for chapter in kept:
        print(f"  #{chapter.index} {chapter.title[:34]}  {len(chapter.content)} 字")

    plain = "\n".join(
        piece for chapter in kept for piece in (chapter.title, chapter.content)
    ) + "\n"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    utf8_path = OUT_DIR / f"{BOOK_TITLE}.txt"
    utf8_path.write_text(plain, encoding="utf-8")
    gbk_path = OUT_DIR / f"{BOOK_TITLE}_gbk.txt"
    gbk_path.write_bytes(plain.encode("gbk"))

    print(f"\n写入 {utf8_path}  （{utf8_path.stat().st_size} 字节，UTF-8）")
    print(f"写入 {gbk_path}（{gbk_path.stat().st_size} 字节，GBK）")
    print("\n下一步：")
    print(f"  booksoul ingest {utf8_path}")
    print(f"  booksoul identify {BOOK_TITLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
