# 书魂 BookSoul

> 从小说里抽出「立体角色」，然后和书里的角色对话。

## 这是什么

BookSoul 想验证两个假设：

1. 从一部长篇小说里，能抽出**像样的立体角色**（不是通用模板套话）；
2. 和「书里的角色」对话，比和陌生 UGC 角色对话**更有感觉**。

完整设计见 `PROJECT_DESIGN.md`，十阶段实施计划见 `MVP_PLAN.md`，
实现纪律（红线 / 人工把关点 / 设计空白清单）见 `HANDOFF.md`。

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| **1** | **脚手架与数据契约** | ✅ **已完成** |
| 2 | TXT 解析与分章 | ⬜ 未开始 |
| 3 | 角色识别（粗扫） | ⬜ 未开始 |
| 4 | 角色抽取 🔴 风险点 1 | ⬜ 未开始 |
| 5 | 角色卡组装与校验 | ⬜ 未开始 |
| 6 | 最简对话运行时 🔴 风险点 2 | ⬜ 未开始 |
| 7 | 最简记忆（检索注入） | ⬜ 未开始 |
| 8 | 交互界面（CLI） | ⬜ 未开始 |
| 9 | 端到端串联与验收 | ⬜ 未开始 |
| 10 | 评估基线与复盘 | ⬜ 未开始 |

阶段 1 交付的是**可运行骨架 + 一次到位的数据契约**（改动成本最高的部分）：

- `src/booksoul/schema/character.py`：`CharacterCard` / `LoreEntry` / `TimelineEvent` /
  `Relation` / `PlotNode`，严格按 `PROJECT_DESIGN.md` §6.4
- `src/booksoul/schema/tavern.py`：`to_tavern_v2()` / `from_tavern()`，
  含 `extensions.booksoul` 映射与多形态导入兼容（§6.2）
- `src/booksoul/config.py`：环境变量 / 模型名 / 路径
- `src/booksoul/cli.py`：`booksoul version` / `booksoul config` 自检命令（业务命令在阶段 8）

## 目录结构

```
D:\story\
├── PROJECT_DESIGN.md            设计文档（数据契约见 §6）
├── MVP_PLAN.md                  十阶段实施计划
├── HANDOFF.md                   实现交接简报（红线 / 纪律）
├── pyproject.toml
├── .gitignore
├── README.md
├── src/booksoul/
│   ├── config.py                ✅ 阶段1：环境变量 / 模型名 / 路径
│   ├── cli.py                   🚧 阶段8：CLI（阶段1 只有 version / config）
│   ├── schema/
│   │   ├── character.py         ✅ 阶段1：CharacterCard 等
│   │   └── tavern.py            ✅ 阶段1：v2/v3 导入导出
│   ├── llm/client.py            ⬜ 阶段3：LiteLLM 封装（重试/超时/usage）
│   ├── ingest/parser.py         ⬜ 阶段2：TXT 解析分章
│   ├── extract/identify.py      ⬜ 阶段3：角色识别
│   ├── extract/extract.py       ⬜ 阶段4：角色抽取
│   ├── assemble/builder.py      ⬜ 阶段5：组装 + 校验
│   ├── runtime/dialogue.py      ⬜ 阶段6：对话循环
│   └── runtime/memory.py        ⬜ 阶段7：最简检索
├── tests/                       阶段1 单测（59 个用例）
├── data/{novels,cards,sessions,memory}/    产物目录（内容不入 git）
└── evals/golden_set.jsonl       ⬜ 阶段10
```

## 快速开始

环境：**Python 3.14.3**（本机已装）+ `pip` + venv（`MVP_PLAN.md` §1 技术约定）。

```powershell
# 1. 建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 装依赖（阶段 1 只需要标准契约层；dev 里是 pytest）
pip install -e ".[dev]"

# 3. 自检
booksoul version
booksoul config
```

> **本机注意**：当前这台机器**无法访问 PyPI**（沙箱内网络关闭），
> 因此 `.venv` 是用 `--system-site-packages` 建的，直接复用系统已装的
> `pydantic / typer / rich`。有网络时按上面三条命令正常装即可。

## 测试

阶段 1 验收标准是 **`pytest` 全绿**：

```powershell
pytest                     # 装了 pytest 时的正路
python tests/run_tests.py  # 自动选择：pytest 优先，缺 pytest 时回退内置运行器
python tests/run_tests.py --local   # 强制用内置运行器
```

`tests/run_tests.py` + `tests/_pytest_stub.py` 是**本机无 pytest 且无法联网时的兜底**：
测试本身是标准 pytest 风格（fixture / monkeypatch / tmp_path / `pytest.raises`），
装了 pytest 后这两个文件会自动让位，不需要改任何测试代码。

覆盖内容：

- 序列化往返（`model_dump` / `model_dump_json` / JSON 文本）
- 立体字段默认值、必填校验、未知字段忽略
- v2 导出结构（`spec` / `spec_version` / `data.*` / `data.extensions.booksoul`）
- 导入兼容三种形态：有 `spec`+`data` 包装、有 `spec` 但字段平铺、裸字段老卡
- `character_book` 两种写法（v1 列表 / v2 `{"entries": [...]}`）与脏数据兜底
- 配置：环境变量优先、显式传参覆盖、`DATA_DIR` 默认与覆盖、Key 缺失的可操作报错
- CLI：`version` / `config` 冒烟（`config` 不打印 Key 明文）

## 数据契约要点（`PROJECT_DESIGN.md` §6）

**标准字段**（酒馆 v2/v3 兼容）直出 `data.*`：`name`、`description`、`personality`、
`scenario`、`first_mes`、`mes_example`、`character_book`、`creator_notes`、`tags`、
`character_version`。

**立体扩展**（本项目特有）统一收进 `data.extensions.booksoul`：
`desire`（欲望）、`flaw`（缺陷）、`secret`（秘密）、`timeline`、`relations`、`plot_nodes`。

```json
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": {
    "name": "沈知舟",
    "description": "...",
    "first_mes": "...",
    "extensions": {
      "booksoul": {
        "desire": "...", "flaw": "...", "secret": "...",
        "timeline": [], "relations": [], "plot_nodes": []
      }
    }
  }
}
```

三条约定：

1. **导入绝不因为缺字段而崩** —— 所有非必填字段都给默认值，缺 `extensions` 也能导入。
2. **`relations` 单向存储** —— 每条记为 `from → to`，JSON 键是 `from`，
   Python 字段名是 `from_`（`from` 是关键字）；不做双向冗余（§6.3）。
3. **v2 条目里的陌生键不丢** —— 导入时存进 `extraction_meta["lore"]`，导出时合回去，
   保证往返不收敛（也不膨胀）。

## 全局工程约定（所有阶段遵守）

- 所有 LLM 调用统一走 `llm/client.py`，**业务代码里不允许直接调 SDK**。
- 所有中间产物**落盘缓存**（识别结果、抽取结果），避免重复花钱。
- LLM 返回的结构化数据 → **Pydantic 校验 + 失败重试一次**；仍失败则报错退出，不静默吞掉。
- 每个阶段的产物路径固定，便于后续阶段复用与人工检查。
- 中文文本先做编码探测（utf-8 / gbk / gb18030）。
- **API Key 只读环境变量 `DEEPSEEK_API_KEY`**，不写进代码、不进 git。

## 红线（`HANDOFF.md` §2）

不引入 LangChain / LangGraph、数据库 / 向量库 / embedding；不做被砍掉的功能
（mood / 剧情推进 / 关系状态机 / 多角色 / 调教 UI / 评估体系）；不提前做优化；
不做 Web 前端。

## License

私有项目（尚未决定开源协议）。
