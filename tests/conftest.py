"""pytest 引导 + 共享夹具。

测试用的角色与文本均为**自造样例**，不依赖任何真实小说语料。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from booksoul.schema import (  # noqa: E402  (必须在 sys.path 兜底之后导入)
    CharacterCard,
    LoreEntry,
    PlotNode,
    Relation,
    TimelineEvent,
)


def make_full_card() -> CharacterCard:
    """带全部立体字段的角色卡（用于往返测试）。"""
    return CharacterCard(
        name="沈知舟",
        description="青云宗大师兄，剑术第一，惯穿月白长衫，说话总留半句。",
        personality="外在冷峻克制，内里护短；不擅长解释自己。",
        scenario="后山剑冢，夜雨。",
        first_mes="「站在那儿别动。」他没回头，剑尖挑开雨幕。",
        mes_example=(
            "<START>\n"
            "{{user}}: 师兄，你为什么要替我挡那一剑？\n"
            "{{char}}: 手滑。\n"
            "{{user}}: ……手滑会滑到剑锋上？\n"
            "{{char}}: 再多问一句，我就把你丢给二师弟。"
        ),
        character_book=[
            LoreEntry(
                keys=["剑冢", "后山"],
                content="剑冢埋着青云宗历代叛徒的断剑，夜里会有剑鸣。",
                insertion_order=10,
            ),
            LoreEntry(
                keys=["月白长衫"],
                content="月白长衫是掌门亲赐，只有首席弟子能穿。",
                enabled=False,
            ),
        ],
        creator_notes="从《青云旧事》第 3、7、12 章抽取。",
        tags=["古风", "师兄", "仙侠"],
        character_version="1.2",
        desire="想让师妹活着离开青云宗，哪怕代价是自己留下。",
        flaw="把「不解释」当成保护别人的方式。",
        secret="当年剑冢那一夜，是他亲手放走了叛徒。",
        timeline=[
            TimelineEvent(
                id="t1",
                order=1,
                summary="雨夜剑冢初遇，替师妹挡下一剑。",
                actors=["沈知舟", "师妹"],
                chapter="第三章",
                quote="「站在那儿别动。」",
            ),
            TimelineEvent(id="t2", order=2, summary="掌门赐月白长衫。", actors=["沈知舟"]),
        ],
        relations=[
            Relation(
                **{
                    "from": "沈知舟",
                    "to": "师妹",
                    "kind": "师兄妹",
                    "description": "表面冷淡，实则一路暗中照拂。",
                    "evolution": ["初遇时只当她是累赘", "剑冢之后开始护着她"],
                }
            ),
            Relation(from_="沈知舟", to="掌门", kind="师徒", description="敬而远之。"),
        ],
        plot_nodes=[PlotNode(id="p1", order=1, title="雨夜剑冢", description="开场名场面。")],
        source_book="青云旧事",
        extraction_meta={
            "model": "deepseek/deepseek-chat",
            "quotes": {
                "desire": "「你活着出去，比什么都强。」（第 12 章）",
                "flaw": "「不必问。」（第 7 章）",
            },
        },
    )


@pytest.fixture()
def full_card() -> CharacterCard:
    return make_full_card()


@pytest.fixture()
def raw_wrapped_card() -> dict[str, Any]:
    """形态 ①：chub.ai 主流包装（spec + data + extensions.booksoul）。"""
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "沈知舟",
            "description": "青云宗大师兄。",
            "personality": "冷峻护短。",
            "scenario": "后山剑冢。",
            "first_mes": "「站在那儿别动。」",
            "mes_example": "<START>\n{{user}}: 师兄\n{{char}}: 手滑。",
            "creator_notes": "从《青云旧事》抽取。",
            "tags": ["古风"],
            "character_version": "1.2",
            "character_book": {
                "entries": [
                    {
                        "id": 7,
                        "keys": ["剑冢"],
                        "content": "埋着断剑。",
                        "enabled": True,
                        "insertion_order": 10,
                        "position": "before_char",
                        "extensions": {"book": "upstream"},
                    }
                ]
            },
            "extensions": {
                "booksoul": {
                    "desire": "让师妹活着离开。",
                    "flaw": "不解释。",
                    "secret": "他放走了叛徒。",
                    "timeline": [
                        {"id": "t1", "order": 1, "summary": "雨夜剑冢", "actors": ["沈知舟"]}
                    ],
                    "relations": [
                        {"from": "沈知舟", "to": "师妹", "kind": "师兄妹", "description": "照拂。"}
                    ],
                    "plot_nodes": [],
                    "source_book": "青云旧事",
                    "extraction_meta": {"quotes": {"flaw": "「不必问。」"}},
                }
            },
        },
    }


@pytest.fixture()
def raw_plain_card() -> dict[str, Any]:
    """形态 ②：无 spec、无 extensions 的老卡裸字段格式（含脏数据）。"""
    return {
        "name": "无名剑客",
        "description": "一个只留下背影的人。",
        "personality": "沉默。",
        "first_mes": "「……」",
        "mes_example": "",
        "character_book": [{"keys": ["背影"], "content": "他从不回头。", "enabled": True}],
        "tags": "独行",  # 故意用 str 而不是 list：外部卡常见脏数据
        "character_version": None,
    }


# ────────────────────────── 阶段 2：小说语料 ──────────────────────────


def make_sample_novel_text() -> str:
    """一段"格式规整"的样例小说：**程序计数**可知有 5 章。

    故意混入盗版站广告行、连续空行、`\\r\\n` 换行，用来验证清洗。

    自带"目测校准"：每章正文都是 ``第{i}章正文。`` 重复 `REPEAT` 次，
    所以章节数与每章字数都能一眼数出来。
    """
    repeat = 40
    body = {i: f"第{i}章正文。" * repeat for i in range(1, 6)}
    return (
        "《青云旧事》\n"
        "作者：无名\n"
        "请记住本站域名 www.example-ad.com\n"
        "\r\n"
        "\r\n"
        f"第一章 雨夜\n{body[1]}\n"
        "\n"
        "手机用户请访问 m.example-ad.com\n"
        f"第二章 剑冢\n{body[2]}\n"
        "\n"
        "\n"
        "\n"
        f"第三章 旧约\n{body[3]}\n"
        "最新章节目录 请记住本站\n"
        f"第四章 叛徒\n{body[4]}\n"
        f"第五章 长夜\n{body[5]}\n"
        "（本章完）\n"
    )


@pytest.fixture()
def sample_novel_text() -> str:
    return make_sample_novel_text()


#: 「真实短篇」语料的章节定义：**(标题, 每章一句, 重复次数)**。
#: 章节数直接由这个列表长度给出，所以"目测一致"是可以程序化断言的。
REAL_STORY_CHAPTERS: tuple[tuple[str, str, int], ...] = (
    ("第一章 雨夜", "剑冢的雨下了整夜。沈知舟站在碑前，月白长衫湿透了。", 24),
    ("第二章 旧约", "「你答应过我的。」师妹的声音在雨里发抖。他没有回头。", 20),
    ("第三章 断剑", "断剑埋在三尺土下。他挖了一夜，指甲缝里全是血。", 16),
    ("第四章 叛徒", "掌门问他为什么放人。他说手滑。掌门笑了，笑得很难看。", 18),
    ("第五章 长夜", "长夜将尽的时候，他终于说出了那个名字。", 12),
    ("第六章 出山", "山门在身后关上。他没有回头，一次都没有。", 14),
)

#: 真实语料里混入的盗版站噪声（清洗必须干掉它们）。
REAL_STORY_NOISE = (
    "\n（本章完）\n最新章节目录 请记住本站\nhttps://spam.example.com/x\n"
)


def make_real_story_text() -> str:
    """一段"格式规整"的样例短篇：章节头 + 盗版站噪声 + 长短不一的章节。

    章节数 = `len(REAL_STORY_CHAPTERS)`，每章字数由「一句 × 重复次数」给定，
    所以切分结果可以逐章精确断言 —— 这就是阶段 2 验收里"目测一致"的可重复版本。
    """
    head = (
        "《青云旧事》\n"
        "作者：无名氏\n"
        "请记住本站域名 www.example-ad.com\n"
        "手机用户请访问 m.example-ad.com\n"
    )
    body = "\n".join(
        f"\n{title}\n{line * repeat}\n{REAL_STORY_NOISE}"
        for title, line, repeat in REAL_STORY_CHAPTERS
    )
    return head + body


@pytest.fixture()
def real_story_text() -> str:
    return make_real_story_text()


