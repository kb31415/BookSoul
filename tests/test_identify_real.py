"""阶段 3 的**真实调用**验收测试（默认跳过）。

`MVP_PLAN.md` 阶段 3 验收标准：**对样例小说列出的人物，人工检查合理
（前 5 名确实是主要角色）**。

这条标准没法用 mock 证明 —— mock 只能证明"聚合与排序逻辑正确"，
证明不了"LLM 抽出来的人名是对的"。所以这里单独放一个真实调用的用例：

- 默认 **跳过**，不花钱；
- 要跑就设 `BOOKSOUL_REAL_LLM=1`，并保证环境里有 `DEEPSEEK_API_KEY`；
- 语料用公版书《宛如约》前 5 回（`data/novels/宛如约.txt`，由
  `scripts/fetch_corpus.py` 生成），**语料不入 git**。

跑法：

```powershell
$env:BOOKSOUL_REAL_LLM = "1"
pytest tests/test_identify_real.py -v -s
```
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from booksoul.config import load_settings
from booksoul.extract import identify_candidates, merge_aliases
from booksoul.ingest import dump_novel, parse_file
from booksoul.llm import LLMClient
from booksoul.storage import build_repositories

REAL_LLM = os.environ.get("BOOKSOUL_REAL_LLM") == "1"

#: 语料路径（公版书，不入 git）。
CORPUS = Path(__file__).resolve().parents[1] / "data" / "novels" / "宛如约.txt"

#: 《宛如约》前 5 回里**人工确认**的主要人物（见 README 的验收记录）。
#: 名字用繁体，因为语料是繁体；允许简繁两种写法都算命中。
EXPECTED_MAJOR = ("司空約", "趙如子", "趙白")

pytestmark = [
    pytest.mark.skipif(not REAL_LLM, reason="需要 BOOKSOUL_REAL_LLM=1 才跑真实调用（会花钱）"),
    pytest.mark.skipif(not CORPUS.is_file(), reason=f"缺语料 {CORPUS.name}（见 scripts/fetch_corpus.py）"),
]


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    """跑一次真实识别，结果给下面几条用例共用（只花一次钱）。

    `tmp_path_factory` 是 pytest 内置的 **session 级**夹具，模块级夹具可以直接用；
    而 `tmp_path` 是函数级，模块级用例拿不到。
    """
    settings = load_settings()
    if not settings.has_api_key:
        pytest.skip("没有 DEEPSEEK_API_KEY")

    data_dir = tmp_path_factory.mktemp("real")
    repositories = build_repositories(load_settings(data_dir=data_dir))
    novel = parse_file(CORPUS)
    dump_novel(novel, repositories.novels)

    client = LLMClient.from_settings(settings)
    payload, report = identify_candidates(novel, client, repositories, top_n=15)
    groups = merge_aliases(client, {c.name: c.contexts for c in payload.candidates})
    return payload, report, groups, client


def test_top_five_are_major_characters(real_run) -> None:
    """验收标准：前 5 名确实是主要角色。"""
    payload, _, _, _ = real_run
    top5 = payload.names()[:5]
    print(f"\n前 5 名: {top5}")

    def normalized(name: str) -> str:
        return name.replace("約", "约").replace("趙", "赵").replace("學", "学")

    normalized_top5 = [normalized(name) for name in top5]
    for expected in EXPECTED_MAJOR:
        assert normalized(expected) in normalized_top5, f"{expected} 没进前 5：{top5}"


def test_top_candidates_appear_in_multiple_chapters(real_run) -> None:
    """主要角色必须跨章出现 —— 只在一章露脸的不该排在前面。"""
    payload, _, _, _ = real_run

    assert payload.candidates
    assert payload.candidates[0].chapter_count >= 3
    assert payload.candidates[0].mentions >= 3


def test_alias_merge_finds_protagonist_aliases(real_run) -> None:
    """司空約 在文中有"司空學士""默愛"等称法，归并要能认出来（§4）。"""
    _, _, groups, _ = real_run
    print("\n归并结果:", [(g.main, g.aliases) for g in groups])

    merged = {group.main: group.aliases for group in groups}
    assert merged, "至少应该合并出一组别名"
    assert any(len(aliases) >= 2 for aliases in merged.values())


def test_real_run_reports_usage(real_run) -> None:
    """真实跑完要有 token 账（阶段 10 成本量化要用）。"""
    payload, _, _, client = real_run
    print("\nusage:", client.usage.as_dict())

    assert payload.usage["calls"] >= 5
    assert payload.usage["prompt_tokens"] > 0
    assert payload.usage["failed_calls"] == 0
