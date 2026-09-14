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
| **2** | **TXT 解析与分章** | ✅ **已完成** |
| **3** | **角色识别（粗扫）** | ✅ **已完成**（含真实调用验收） |
| 4 | 角色抽取 🔴 风险点 1 | ⬜ 未开始 |
| 5 | 角色卡组装与校验 | ⬜ 未开始 |
| 6 | 最简对话运行时 🔴 风险点 2 | ⬜ 未开始 |
| 7 | 最简记忆（检索注入） | ⬜ 未开始 |
| 8 | 交互界面（CLI） | 🚧 已有 `version` / `config` / `ingest` / `identify` |
| 9 | 端到端串联与验收 | ⬜ 未开始 |
| 10 | 评估基线与复盘 | ⬜ 未开始 |

阶段 1 交付的是**可运行骨架 + 一次到位的数据契约**（改动成本最高的部分）：

- `src/booksoul/schema/character.py`：`CharacterCard` / `LoreEntry` / `TimelineEvent` /
  `Relation` / `PlotNode`，严格按 `PROJECT_DESIGN.md` §6.4
- `src/booksoul/schema/tavern.py`：`to_tavern_v2()` / `from_tavern()`，
  含 `extensions.booksoul` 映射与多形态导入兼容（§6.2）
- `src/booksoul/config.py`：环境变量 / 模型名 / 路径
- `src/booksoul/cli.py`：`booksoul version` / `booksoul config` 自检命令（业务命令在阶段 8）
- `src/booksoul/storage/`：**存储抽象**（`MVP_PLAN.md` §1「迭代友好性约束」）
  - 四个窄接口：`NovelRepository` / `CardRepository` / `MemoryRepository` / `SessionRepository`
  - 文件实现：`FileNovelRepository` / `FileCardRepository` / `FileMemoryRepository` /
    `FileSessionRepository`，`build_repositories()` 一次性装配
  - 数据契约：`NovelRecord` / `MemoryEntry`（§13.6 逐字段对齐）/ `SessionLog`（含预留 `state`）
  - **业务代码只依赖接口**，不许直接 `open("data/...json")` —— 让「文件 → Postgres + pgvector」
    只是换实现（有静态检查用例守着这条红线）

阶段 2 交付的是**确定性文本摄取**（无 LLM 调用）：

- `src/booksoul/ingest/parser.py`
  - **编码探测**：BOM 优先，其次按 `utf-8 → gb18030 → big5` **先严后宽**严格解码；
    全失败则 `gb18030 + replace` 兜底（不阻塞主流程）
  - **清洗**：统一换行、去盗版站广告/导航行、压缩连续空行（**保留**段落边界）
  - **分章**：正则匹配 `第X章/节/回/篇/卷/集/幕` + `序章/楔子/尾声/番外` 等；
    **找不到标题就按长度在句子边界切**（`MVP_PLAN.md` 风险预案要求的兜底）
  - `Chapter{index, title, content}` / `Novel` 数据契约，落盘 `data/novels/{book_id}.json`
  - `split_long_chapters()`：把超长章节切到 ≤6000 字（`PROMPT_DESIGN.md` §3 的下游约束）

阶段 3 交付的是**逐章人物识别 + 别名归并**（Prompt 原文照搬 `PROMPT_DESIGN.md` §3/§4）：

- `src/booksoul/llm/client.py`：LiteLLM 封装
  - **可注入** `completion_fn` —— 业务逻辑与网络解耦，单测不花钱
  - 重试按 `1s / 2s` 退避；**重试耗尽抛 `LLMCallError`，绝不静默吞掉**
  - 累计 usage（prompt / completion / **缓存命中** / 失败次数），阶段 10 成本量化直接读
- `src/booksoul/extract/identify.py`
  - `scan_chapter()`：Prompt 1 逐章抽人名，单章 >6000 字自动**分块**
  - **按章落盘缓存** `data/cards/{book_id}/chapters/{i}.names.json`，带内容指纹 ——
    正文没变就跳过，重跑不烧 token
  - `rank_candidates()`：按**出现章数 + 出现次数**排序取前 N（并列时按名字稳定排序）
  - `merge_aliases()`：Prompt 2 别名归并，上下文取每个候选的 2–3 句原文
  - 产物：`candidates.json` + `name_contexts.json`
- 新增 CLI：`booksoul ingest <file>` / `booksoul identify <book_id>`


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
│   ├── cli.py                   🚧 阶段8：CLI（已实现 version/config/ingest/identify）
│   ├── storage/
│   │   ├── models.py            ✅ 阶段1：NovelRecord / ChapterNameCache / MemoryEntry / SessionLog
│   │   └── repository.py        ✅ 阶段1：4 个 Repository 接口 + File 实现
│   ├── schema/
│   │   ├── character.py         ✅ 阶段1：CharacterCard 等
│   │   └── tavern.py            ✅ 阶段1：v2/v3 导入导出
│   ├── llm/client.py            ✅ 阶段3：LiteLLM 封装（重试/超时/usage）
│   ├── ingest/parser.py         ✅ 阶段2：TXT 编码探测 / 清洗 / 分章
│   ├── extract/identify.py      ✅ 阶段3：角色识别 + 别名归并
│   ├── extract/extract.py       ⬜ 阶段4：角色抽取
│   ├── assemble/builder.py      ⬜ 阶段5：组装 + 校验
│   ├── runtime/context.py       ⬜ 阶段6：assemble_context(...)（结构红线 ②）
│   ├── runtime/dialogue.py      ⬜ 阶段6：对话循环
│   └── runtime/memory.py        ⬜ 阶段7：最简检索
├── scripts/fetch_corpus.py      真实验收语料抓取（公版书，产物不入 git）
├── tests/                       阶段1–3 单测（298 个用例）
├── data/{novels,cards,sessions,memory}/    产物目录（内容不入 git）
└── evals/golden_set.jsonl       ⬜ 阶段10
```

## 快速开始

环境：**Python 3.14.3**（本机已装）+ `pip` + venv（`MVP_PLAN.md` §1 技术约定）。

```powershell
# 1. 建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 装依赖（litellm 属 [llm] 额外依赖，阶段 3 起需要）
pip install -e ".[dev,llm]"

# 3. 配 API Key（只读环境变量，不写进代码；详见下节）
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-你的key", "User")

# 4. 自检
booksoul version
booksoul config          # 应显示 API Key 已设置
```

### 跑通全流程（阶段 2 → 3）

```powershell
# 抓公版语料（《宛如约》前 5 回；产物在 data/ 下，不入 git）
python scripts/fetch_corpus.py

booksoul ingest   data/novels/宛如约.txt   # 阶段2：分章
booksoul identify 宛如约                    # 阶段3：候选人名 + 别名归并（会调 API）
```

### API Key 说明

Key **只从环境变量读**（`config.py`），不写进代码、不进 git。取值顺序：

1. **进程环境变量** `DEEPSEEK_API_KEY`
2. **平台回退**：Windows 用户级环境变量（`SetEnvironmentVariable(..., "User")` 写的那层）

第 2 层是为了绕开「环境变量只对**新启动的进程**可见」这个坑 —— 用户设完 Key，
不必重启终端 / 编辑器就能用。测试默认把这层关掉（`conftest.py` 的 autouse 夹具），
否则本机的真实 Key 会污染「缺 Key 应该报错」的用例。


> **网络说明（本机）**：`pypi.org` 可直连，`github.com` 直连被墙但本机
> `127.0.0.1:7890` 有代理可用。本仓库已写好 **local** 代理配置（只影响这个仓库）：
>
> ```powershell
> git config --local http.proxy  http://127.0.0.1:7890
> git config --local https.proxy http://127.0.0.1:7890
> ```
>
> 没有代理的机器上执行 `git config --local --unset http.proxy`（`https.proxy` 同理）即可。

## 测试

阶段 1 验收标准是 **`pytest` 全绿**（实测 59 passed）：

```powershell
pytest                     # 正路（已装 dev 依赖时用它）
python tests/run_tests.py  # 自动选择：pytest 优先，缺 pytest 时回退内置运行器
python tests/run_tests.py --local   # 强制用内置运行器
```

`tests/run_tests.py` + `tests/_pytest_stub.py` 是**兜底机制**，为「装了项目但没装
pytest、且一时无法联网装」的场景准备：前者的 `pytest` 优先策略会自动让位，
后者只在 `import pytest` 失败时才顶上去。**装了 pytest 后这两个文件完全静默**，
测试代码一行都不用改 —— 它们的价值只是"没有 pytest 也跑得起来"，不是替代品。

覆盖内容：

**阶段 1（数据契约 + 存储抽象）**

- 序列化往返（`model_dump` / `model_dump_json` / JSON 文本）
- 立体字段默认值、必填校验、未知字段忽略
- v2 导出结构（`spec` / `spec_version` / `data.*` / `data.extensions.booksoul`）
- 导入兼容三种形态：有 `spec`+`data` 包装、有 `spec` 但字段平铺、裸字段老卡
- `character_book` 两种写法（v1 列表 / v2 `{"entries": [...]}`）与脏数据兜底
- 配置：环境变量优先、显式传参覆盖、`DATA_DIR` 默认与覆盖、Key 缺失的可操作报错
- CLI：`version` / `config` 冒烟（`config` 不打印 Key 明文）
- **Repository**：四类数据的 CRUD、接口契约（`isinstance` 校验 Protocol）、
  路径穿越防护、坏文件/坏行宽容读取、记忆同内容去重与 `hits` 累加、
  session 的 `state` 写入读回、JSONL append-only 形态，
  以及**红线静态检查**（业务代码里不许出现直接 `data/` 文件访问）

**阶段 2（TXT 解析与分章）**

- 编码探测：utf-8 / utf-8-sig（BOM）/ gbk / gb18030 / utf-16 / 纯 ASCII / 空文件 /
  垃圾字节兜底；并锁死 **utf-8 必须排在 gb18030 之前**（否则中文会解成乱码）
- 分章：20 种标题写法（含全角数字、`第X节/回/篇/卷/集/幕`、`序章/楔子/尾声/番外`）
  与 11 种**反例**（`第一章节的写法…`、`序章的写法…`、`最后一章里他死了。` 等不得误判）
- 兜底：无标题按长度在句子边界切、无标点长串、全空章节
- 清洗：22 条广告/导航行必须删、15 条正常叙述**不得误删**
- 验收（`test_parser_acceptance.py`）：章节数与人工目测一致、每章字数逐章精确核对、
  **同一文本的 utf-8 与 gbk 解析结果逐字段一致**、超长章节切到 ≤6000 字（`PROMPT_DESIGN` §3）
- `data/novels/*.txt` 若有真实语料，会自动解析并打印统计供人工目测（没有则跳过）

**阶段 3（角色识别）**

- 解析与解析器：Prompt 1 输出三种形态兜底、名字去包裹符号（`「」《》`）、
  别名组校验（主名不得同时是别名、无主名的组丢弃）
- 缓存：同内容重跑 **0 次 LLM 调用**、正文改动触发重扫、`--force` 忽略缓存、
  缓存文件名严格是 `{book_id}/chapters/{i}.names.json`
- 分块：超长章节按块多次调用，**同章跨块的出现次数累加、章数仍算 1**
- 排序：出现章数 → 出现次数 → 名字（稳定）；`top_n` 截断；空输入
- 上下文抽样：只取含该名字的句子、跨章按顺序、缺席返回空
- LLM 客户端：重试退避 `1s/2s`、重试耗尽抛 `LLMCallError`、usage 累计、
  `cached_tokens` 字段多路兜底、JSON 代码围栏剥离、`response_format` 传递
- 失败传播：LLM 一直失败必须抛错，**不允许静默产出残缺结果**

**阶段 3 真实验收记录**（`test_identify_real.py`，需 `BOOKSOUL_REAL_LLM=1`）

语料：公版《宛如约》前 5 回（`scripts/fetch_corpus.py` 抓取，2.5 万字）。
识别结果与人工核对一致：

| 排名 | 姓名 | 出现章数 | 人工判断 |
|---|---|---|---|
| 1 | 司空約 | 4 | 男主角 ✅ |
| 2 | 趙如子 | 4 | 女主角 ✅ |
| 3 | 趙白 | 4 | 主要角色 ✅ |
| 4 | 司空學士 | 3 | 男主之父 ✅ |
| 5 | 趙媽媽 | 2 | 女主之母 ✅ |

别名归并也正确（连**繁简混用**都认出来了）：
`司空約 ← 司空學士/司空学士`、`趙如子 ← 如子/小月老牛馬走（诗尾署名）`。

实测成本：6 次调用（5 章 + 1 次归并）约 2.3 万 prompt token，**约 2–3 分钱**；
第二轮重跑 5 章全部命中本地缓存，只花 1 次归并调用。


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

## 花钱纪律（本项目最贵的一条教训）

> **先修 bug，再花钱跑 API。**

真实事故：《咎由自取》85 章抽周燎时，`{existing_persona}` 把累积关系原文全倒进
每次调用，prompt 逐章膨胀、总量 O(n²)。**诊断数据当时已经摆在眼前**
（第 0 章 3,299 字符 → 第 19 章 19,420 字符），却仍然先把整本跑完 —— 花了约
**2.4 元**，是修好之后的 **11 倍**（正常约 2 毛）。

固化成流程（与 §1「不提前做优化」不冲突：这管的是**已知会返工的缺陷**，不是性能优化）：

1. **跑真实 API 之前**，先用假客户端（`completion_fn` 注入）量清楚：
   调用次数 × 单次 prompt 规模 → 估算 token 与费用。
2. 发现任何"会导致返工/重复花钱"的缺陷 → **先修 + 补回归测试**，再跑。
3. 估算费用超出预期时，**先报告再跑**，不擅自开跑。
4. 跑完核对实际用量与预估是否一致；不一致就停下来查原因（不一致本身就是 bug 信号）。

## 全局工程约定（所有阶段遵守）

- 所有 LLM 调用统一走 `llm/client.py`，**业务代码里不允许直接调 SDK**。
- 所有中间产物**落盘缓存**（识别结果、抽取结果），避免重复花钱。
- LLM 返回的结构化数据 → **Pydantic 校验 + 失败重试一次**；仍失败则报错退出，不静默吞掉。
- 每个阶段的产物路径固定，便于后续阶段复用与人工检查。
- 中文文本先做编码探测（utf-8 / gbk / gb18030）。
- **API Key 只读环境变量 `DEEPSEEK_API_KEY`**，不写进代码、不进 git。

## 结构红线（`HANDOFF.md` §2「结构红线」）

> **原则**：MVP 可以砍「实现」，但不能砍「数据契约」和「模块边界」。
> 判断某个简化安不安全，只看一个问题：**它改变了数据契约或模块边界吗？**

| # | 必须留 | 不许出现的样子 | 本项目怎么落 |
|---|---|---|---|
| ① | Repository 抽象接口 | 业务代码里 `open("data/...json")` | `storage/repository.py` 四接口 + File 实现，`dump_novel()` 收 Repository 而非目录 |
| ② | Context Assembler 独立模块 | `dialogue.py` 里直接拼 messages | ⬜ 阶段 6 落 `runtime/context.py` 的 `assemble_context(...)` |
| ③ | 记忆用条目结构 | 记忆存成裸 messages 数组 | ✅ `MemoryEntry`（§13.6 逐字段对齐），MVP 全填 `tier="heuristic"` |
| ④ | session 预留 `state` | session 里没有 `state` | ✅ `SessionLog.state: dict = {}`，有写入读回用例 |

## 红线（`HANDOFF.md` §2）

不引入 LangChain / LangGraph、数据库 / 向量库 / embedding；不做被砍掉的功能
（mood / 剧情推进 / 关系状态机 / 多角色 / 调教 UI / 评估体系）；不提前做优化；
不做 Web 前端。

## 已知问题 / 下一步

**阶段 2 分章的已知缺口**（真实语料实测发现，不阻塞当前流程）

- 用公版《豆棚闲话》试分章时**切不出章节**：该书用「第X则」而非「第X回/章」，
  而 `第X则` 不在 `MVP_PLAN.md` 阶段 2 规定的匹配集里
  （原文只要求 `第X章/节/回`）。
- 兜底生效了（按长度切成 30 块，不崩），所以不阻塞主流程，但章节边界丢失。
- 处理方式：**记录待定**，等阶段 9 跑真实短篇时若再遇到就补。
  这是"要不要扩展匹配集"的产品判断，不自己发明（`HANDOFF.md` §5）。

**阶段 3 抽人名的小噪声**（不影响前 5 名排序）

- 偶尔混入泛称（如 `書童`）—— Prompt 1 规则 3 明确禁止，属 LLM 执行不严。
- 偶尔把典故人物当成本书角色（如 `子房` = 张良）。
- 影响：候选表尾部几位不够干净。前 5 名未受影响，别名归并也已覆盖大部分。

## License

私有项目（尚未决定开源协议）。
