"""Prompt 模板加载测试（`PROMPT_DESIGN.md` §11）。

设计把模板交给实现方时明确了：「Prompt 作为 `.md` 文件读取（便于随时改，
不用改代码）；变量用 `str.format()` 或简单模板替换」。

`str.format()` 在本项目**用不了** —— 模板里同时有 `{{user}}`（要原样保留的酒馆
占位符）和 JSON 示例 `{"personality": ""}`，`format` 会把它们全吃掉。
所以这里测的是"只替换显式变量"的实现。
"""

from __future__ import annotations

import json

import pytest

from booksoul.prompts import (
    PROMPT_FILES,
    available_prompts,
    get_prompts_dir,
    load_prompt,
    render_prompt,
)


def test_all_four_templates_exist() -> None:
    """§11 交付的四个 Prompt 模板都要在仓库里。"""
    assert set(PROMPT_FILES) == {
        "identify_names",
        "merge_aliases",
        "extract_persona",
        "generate_card_fields",
    }
    assert all(available_prompts().values())


def test_prompts_dir_is_repo_root() -> None:
    assert (get_prompts_dir() / "extract_persona.md").is_file()


def test_load_prompt_unknown_name() -> None:
    with pytest.raises(KeyError):
        load_prompt("没有这个模板")


def test_load_prompt_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("extract_persona", prompts_dir=tmp_path)


def test_html_comment_is_stripped() -> None:
    """模板顶部的 `<!-- 变量表 -->` 是给人看的，不该进 prompt。"""
    text = load_prompt("extract_persona")

    assert not text.startswith("<!--")
    assert "变量用" not in text.splitlines()[0]
    assert text.startswith("你是文学人物分析师。")


def test_render_only_replaces_declared_variables() -> None:
    rendered = render_prompt(
        "extract_persona",
        character_name="沈知舟",
        aliases="沈师兄",
        chapter_index=1,
        chapter_title="雨夜",
        existing_persona="（无，这是首次抽取）",
        relevant_passages="沈知舟立在廊下。",
    )

    assert "【角色】沈知舟" in rendered
    assert "【别名】" in rendered and "沈师兄" in rendered
    assert "第 1 章 雨夜" in rendered
    assert "沈知舟立在廊下。" in rendered


def test_render_preserves_json_example_braces() -> None:
    """输出格式里的 JSON 示例必须原样保留（`str.format` 会在这里炸）。

    模板里是多行 JSON（2 空格缩进），所以逐行断言存在性，而不是拼成一个
    单行字符串去匹配。
    """
    rendered = render_prompt(
        "extract_persona",
        character_name="甲",
        aliases="",
        chapter_index=0,
        chapter_title="第一章",
        existing_persona="",
        relevant_passages="",
    )

    for line in (
        '"personality": "",',
        '"desire": "",',
        '"flaw": "",',
        '"secret": "",',
        '"speech_style": "",',
        '"relationships": [{"target": "", "change": ""}],',
        '"quotes": [',
        '"confidence": "explicit"',
        '"speech_samples": [',
        '"changes": [{"field": "", "from": "", "to": "", "reason": "原文依据"}]',
    ):
        assert line in rendered, line

    # 整段 JSON 示例仍然可解析（花括号没被替换吃掉）
    block = rendered.split("严格输出以下 JSON，不要任何额外文字、不要 markdown 代码块标记：")[1]
    block = block.split("【章节文本中与")[0].strip()
    json.loads(block)


def test_render_preserves_double_braces_placeholders() -> None:
    """`{{user}}` / `{{char}}` 是酒馆占位符，必须**原样**保留（不能被压成 `{user}`）。"""
    rendered = render_prompt(
        "generate_card_fields",
        final_persona="性格：冷峻",
        speech_samples="- 「不必问。」",
    )

    assert "{{user}}" in rendered
    assert "{{char}}" in rendered
    assert "{user}" not in rendered.replace("{{user}}", "")
    assert "<START>" in rendered


def test_render_leaves_unknown_placeholders_untouched() -> None:
    rendered = render_prompt("extract_persona", character_name="甲")

    # 没传的变量保持原样（能一眼看出漏填）
    assert "{relevant_passages}" in rendered
    assert "{character_name}" not in rendered


def test_no_variable_is_a_substring_of_another() -> None:
    """变量名之间不允许互为子串 —— 否则替换会互相破坏（真踩过）。

    踩坑记录：`render_prompt` 曾把第一个参数叫 `name`，于是
    `{name}` 命中了 `{character_name}` 内部，把 `{character_name}`
    变成了 `{甲姓名}` 之类的垃圾，prompt 直接废掉。
    """
    import re

    for template in PROMPT_FILES.values():
        text = load_prompt(template.replace(".md", ""))
        placeholders = set(re.findall(r"\{([a-z_]+)\}", text))
        for left in placeholders:
            for right in placeholders:
                if left != right:
                    assert left not in right, f"{template}: {{{left}}} 是 {{{right}}} 的子串"


def test_prompt_matches_design_wording() -> None:
    """关键措辞逐字来自 `PROMPT_DESIGN.md`（实现方不改写设计）。"""
    persona = load_prompt("extract_persona")
    assert "你是文学人物分析师。" in persona
    assert "没有原文支撑的字段，宁可输出空字符串" in persona
    assert "禁止通用套话" in persona
    assert "增量原则" in persona
    assert "不要静默覆盖" in persona
    assert "explicit" in persona and "inferred" in persona

    fields = load_prompt("generate_card_fields")
    assert "你是角色卡设计师。" in fields
    assert "必须留钩子" in fields
    assert "自我介绍式开场" in fields

    names = load_prompt("identify_names")
    assert names.startswith("你是中文小说人物识别专家。")
    assert "{chapter_text}" in names

    aliases = load_prompt("merge_aliases")
    assert "宁可漏合并，不可错合并" in aliases
