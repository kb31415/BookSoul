"""Prompt 模板加载（`PROMPT_DESIGN.md` §11：模板作为 `.md` 文件读取，便于随时改）。

为什么不用 `str.format()`：Prompt 里同时存在两种花括号 ——

- 待填变量：`{character_name}`、`{relevant_passages}` …
- **要原样保留的**花括号：酒馆占位符 `{{user}}` / `{{char}}`，以及输出格式里的
  JSON 示例（`{"personality": ""}`）

`str.format()` 会把这些全当成占位符，`{{user}}` 还会被压成 `{user}` —— 直接毁掉
few-shot 的格式。所以这里**只替换显式声明的变量名**，其余花括号一律不动
（模板保持与设计文档逐字一致，不需要任何转义）。
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DEFAULT_PROMPTS_DIR",
    "PROMPT_FILES",
    "available_prompts",
    "get_prompts_dir",
    "load_prompt",
    "render_prompt",
]

#: 模板目录：`<repo>/prompts`。
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

#: 四个 Prompt 对应的模板文件名（对齐 `PROMPT_DESIGN.md` §11 的交付接口表）。
PROMPT_FILES: dict[str, str] = {
    "identify_names": "identify_names.md",
    "merge_aliases": "merge_aliases.md",
    "extract_persona": "extract_persona.md",
    "generate_card_fields": "generate_card_fields.md",
}


def get_prompts_dir() -> Path:
    """模板目录（默认 `<repo>/prompts`，可用参数覆盖）。"""
    return DEFAULT_PROMPTS_DIR


def _strip_html_comment(text: str) -> str:
    """去掉模板顶部的说明注释（`<!-- ... -->`，只用于文档，不进 prompt）。

    只处理**文件开头**的注释块，避免误删正文里可能出现的 `<!--`。
    """
    stripped = text.lstrip()
    if not stripped.startswith("<!--"):
        return text
    end = stripped.find("-->")
    if end == -1:
        return text
    return stripped[end + 3 :].lstrip("\n")


def load_prompt(prompt_name: str, prompts_dir: str | Path | None = None) -> str:
    """按名字读模板原文（`extract_persona` / `generate_card_fields` …）。

    模板不存在时抛出带路径的 `FileNotFoundError` —— 不静默返回空串
    （否则会拿空 prompt 去调 LLM，白花钱还查不出原因）。

    参数名是 `prompt_name` 而不是 `name`：`render_prompt` 会把关键字参数
    直接当变量名做替换，若这里叫 `name`，`{name}` 会变成
    `{character_name}` / `{chapter_name}` 的**子串**，替换时互相破坏
    （有 `test_no_variable_is_a_substring_of_another` 守门）。
    """
    if prompt_name not in PROMPT_FILES:
        known = "、".join(sorted(PROMPT_FILES))
        raise KeyError(f"未知的 Prompt 名：{prompt_name}（可用：{known}）")

    directory = Path(prompts_dir) if prompts_dir is not None else get_prompts_dir()
    path = directory / PROMPT_FILES[prompt_name]
    if not path.is_file():
        raise FileNotFoundError(f"找不到 Prompt 模板：{path}")

    return _strip_html_comment(path.read_text(encoding="utf-8"))


def render_prompt(
    prompt_name: str,
    prompts_dir: str | Path | None = None,
    **variables: object,
) -> str:
    """读模板并把 `{变量}` 替换成给定值。

    **只替换传进来的键**，模板里其余花括号（`{{user}}`、JSON 示例）原样保留。
    变量名之间不允许互为子串（见 `load_prompt` 的说明）。
    """
    text = load_prompt(prompt_name, prompts_dir)
    for key, value in variables.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def available_prompts(prompts_dir: str | Path | None = None) -> dict[str, bool]:
    """列出四个模板是否存在（自检用）。"""
    directory = Path(prompts_dir) if prompts_dir is not None else get_prompts_dir()
    return {name: (directory / filename).is_file() for name, filename in PROMPT_FILES.items()}
