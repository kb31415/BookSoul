# Agent 项目选型报告（2026-09-11）

> 面向：**Python 为主、场景待定**、想按 GitHub 主流 + 新潮项目来定技术栈的开发者。
> 数据来源：2026 年 2 月–9 月的公开文章、GitHub Trending 拆解、厂商官方博客，以及 2026-09-11 通过 GitHub API 实测的仓库数据。
> 星数/版本为**来源时点数据**，非实时；已在每处标注。凡标注「未验证」的，是二手说法，落地前请自行复核。
> **数据可信度分级**：第 1（编排）、2（记忆）、3（协议）、5（编码 Agent）、6（观测）、7（推理）层的星数/许可/活跃度已用 `api.github.com` 或其镜像 `ungh.cc`/`ecosyste.ms` 逐个实测，关键许可条款直读了 LICENSE 原文；**第 4 层（浏览器使用）仍为二手来源**，未复核。所有「未验证」标记都保留了原样，请勿当成事实使用。

---

## 0. 结论速览（TL;DR）

**别先选框架，先选「你的 agent 要跑多久、崩了怕不怕」。** 2026 年选型的核心分水岭不是功能多寡，而是**有没有持久化状态 + 断点恢复**：

- 任务 **< 1 分钟、无状态、失败重试即可** → 用轻量 SDK，**别上重型编排**。
- 任务 **跨分钟/跨小时、碰钱碰数据、有人工审批** → 必须上有 checkpoint 的图编排。

### 默认推荐（Python，通用业务 Agent）

| 层 | 选型 | 一句话理由 |
|---|---|---|
| 编排 | **LangGraph 1.x** | 生产案例最多、唯一把 durable execution + human-in-the-loop 做成一等公民 |
| 轻量替代 | **Pydantic AI** | 类型安全 + 结构化输出最强，单循环 agent 不必上 LangGraph |
| 厂商原生 | **OpenAI Agents SDK** | 代码最少（16 行），但 pre-1.0 且实质是「选供应商」 |
| 工具层 | **MCP（官方 Python SDK v2，`pip install mcp`）** | 已 Linux Foundation 标准化，是唯一"换框架还能带走"的资产。**v2 是破坏性改版，先读 migration guide** |
| 观测/评估 | **DeepEval（CI 断言）+ OTel 埋点**，有 UI 需求再加 Langfuse | 第一天自托管 Langfuse/Phoenix 等于接第二份运维；埋点先做，迁移零改造 |
| 状态/存储 | **一个 Postgres**（checkpoint + 会话 + 业务库共用，向量先用 pgvector） | 少一个组件，事务与恢复语义一致 |
| UI | **Chainlit**（快）或 **AG-UI 系**（可定制） | Python 原生 + Apache-2.0；别用 Streamlit/Gradio 做长会话 |
| 模型 | 托管 API 起步（LiteLLM 只当 SDK 用） | 自托管 vLLM/SGLang 是规模化后的成本问题，不是选型问题 |

**如果只记一句**：`LangGraph（编排）+ MCP 工具 + 一个 Postgres（checkpoint 与业务共用）+ 埋好 OTel` 是 2026 年 Python 生产栈的最大公约数；单循环小项目用 `Pydantic AI` 起步，**按可量化触发条件（见 4.3）而不是按日历升级**。

---

## 1. 2026 年的三个变化（选型逻辑为什么和 2024 不同了）

### 1.1 从"能不能调工具"变成"状态怎么合并、崩了怎么恢复"

2024 年的对比文章还在测"能不能调用工具"——2026 年这是**所有框架都能满分**的事。有团队做了 9 框架 × 10 次同题实测（同一个模型、同一个单工具 agent），9 个框架全部 10/10 调对工具、10/10 答对：工具调用已经不再是差异点。真正的差异转移到：持久化状态、并行分支合并（reducer）、人在环门控、成本上限、可观测性。

### 1.2 MCP 赢下了工具层，工具代码不再绑框架

MCP 已从 Anthropic 的侧项目变成 **Linux Foundation 标准**，Claude Agent SDK 原生、OpenAI Agents SDK 原生、Google ADK 集成，所有严肃框架都提供了客户端。结论很硬：**今天写工具 = 写 MCP server**。这是整份报告里唯一"无论你选哪个框架都不亏"的投资——编排代码换框架要重写，工具和提示词能带走。

（A2A 是另一条线，解决"agent 之间怎么对话"，已超 150 家组织、进入主流云平台，但**除非你要跨厂商/跨团队协作，现在不必上**。）

### 1.3 "Agent Skills" 成了新的包管理生态（2026 年 8 月最热的现象）

2026 年 8 月 GitHub 月榜 Top 19 中有 13 个与 Agent/技能/上下文/记忆/路由相关；月增星冠军 `mattpocock/skills` 单月 +50,486 星（总量 232.9k），内容主体是 **Markdown + Shell 脚本**。形态极其朴素：

```
my-skill/
├── SKILL.md      # 触发条件、适用场景、指令正文
├── scripts/      # 可执行脚本
└── references/   # 参考文档，按需加载
```

它火的原因是**经济学**：模型能力是租的（按 token 付费），技能是自己的（一次编写、处处复用）。同一批榜单里还有 `obra/superpowers`（约 27.6 万星）、`andrej-karpathy-skills`（约 20.5 万星，实质是一个约束 LLM 常见坑的 CLAUDE.md）、`book-to-skill`（把技术书 PDF 转成技能）等。

> ⚠️ **风险同级别**：技能本质是"指令 + 可执行脚本"，装第三方技能等于往你的开发环境塞未审计代码。腾讯已把 **Skills 扫描和 MCP 扫描做成安全产品**（AI-Infra-Guard）——大厂把它当攻击面了。**自建技能库，不要盲抄。**

---

## 2. 分层选型（七层，逐层给结论）

七层是**七个独立决策**，不要指望一个生态全都最优；层与层之间只在薄接缝上对接（一个配置、一个 import、一次 HTTP 调用）。

### 第 1 层：编排与运行时（最重要的一层）

星数/日期为 **2026-09-11 通过 api.github.com 实测**（标注 estimated 的为 star-history 估算，API 403）。

| 框架 | 语言 | 星数(实测) | 最后推送 | 状态 | 定位 | 什么时候选它 | 坑 |
|---|---|---|---|---|---|---|---|
| **LangGraph** | Python/JS | 41.4k | 2026-09-10 | 1.0 GA 2025-10-22，MIT | 低层图 + 持久化运行时 | 生产级可控工作流、长任务、HITL、可审计 | 概念重；每个 super-step 都写 checkpoint 有延迟成本；默认 InMemorySaver **不跨重启**；`create_react_agent` 已废弃指向 `create_agent` |
| **LangChain** | Python | 146.1k | 2026-09-10 | 1.0 GA 2025-10-22，MIT | 快速起 agent + 1000+ 集成 | 快速起步、集成面最广 | 旧功能甩到 `langchain-classic`，升级要迁移 |
| **CrewAI** | Python | 58.4k | 2026-09-10 | MIT | 角色扮演多智能体 | **原型验证业务价值**（20 分钟出 demo） | 无内置 checkpoint，崩了从头来；单工具 bot 装 856MB/143 包；自主 crew 烧 token、可能循环委派 → **生产用 Flows** |
| **AutoGen** | Python | 60.9k | **2026-04-15** | **维护模式**（2025-10 起只修 bug/安全） | 多智能体对话先驱 | 仅存量维护、研究复现 | 星数是历史存量；**新项目勿用** |
| **AG2**（原班人马 fork） | Python | 4.9k | 2026-09-11 | 未到 1.0，Apache-2.0 | 社区续作，自称 AgentOS | 想留在 AutoGen 范式又要有活跃开发 | 生态小；v0.12→v1.0 期间有 legacy API 废弃 |
| **OpenAI Agents SDK** | Python/TS | 29.3k | 2026-09-10 | **0.x（未 1.0）**，MIT | 最小原语（agent/handoff/guardrail/session） | 已决定用 OpenAI 模型、要最短路径 | 抽象薄，复杂控制流自己搭；追踪/托管工具/沙箱都在 OpenAI 侧；38 个未关闭 issue（triage 最健康） |
| **Pydantic AI** | Python | 19.9k | 2026-09-11 | v1 于 2025-09，MIT | 端到端类型化 agent | 单循环 agent、必须向下游返回验证过的数据 | 集成数量少于 LangChain；issue 多（860）；版本换代有迁移成本（另有报道称 2026-06 有 v2/capabilities，**未验证**） |
| **smolagents** | Python | 29.3k | 2026-08-25 | Apache-2.0 | 极简「用代码思考」 | 最轻依赖、本地小模型、教学 | 刻意少功能（无持久化/HITL/可观测）；**迭代明显放缓** |
| **Microsoft Agent Framework** | .NET/**Python** | 13.5k | 2026-09-10 | **1.0 GA 2026-04**，MIT | AutoGen + Semantic Kernel 官方继任者 | Azure/.NET+Python 双栈、从 SK/AutoGen 迁移 | 生态最年轻；一方支持的 provider 仅 8 家；**从 AutoGen 迁移≈架构重写** |
| **Semantic Kernel** | .NET 为主 | 28.6k | 2026-09-09 | 维护/过渡 | 企业级 LLM 集成 SDK | 存量 SK 项目 | 新功能只进 Agent Framework；关键修复至 ~2027-04 |
| **Google ADK** | Python/Java/Go/TS | 21.5k | 2026-09-10 | 1.0 GA 2026-04，Apache-2.0 | 代码优先、跨语言 | GCP/Vertex 栈、A2A 互通、企业多语言 | 与 Google 栈亲和；API 迭代快、文档分散；跑非 Google 模型要额外装 LiteLLM |
| **AWS Strands**（已改名 `harness-sdk`） | Python | 7.2k | 2026-09-11 | Apache-2.0 | model-driven agent harness | AWS/Bedrock 栈、想少写编排 | **仓库改名，旧链接/教程失效**；社区工具包需自行审计；AgentCore Harness 2026-06 GA |
| **Agno** | Python | ~42.1k（estimated） | 2026（未验证） | v3.0.7 | 自带 API+UI 的一体化 agent 平台 | 想要开箱平台 | 企业验证案例少；2→3 大版本破坏性变更 |
| **LlamaIndex** | Python/TS | 52.1k | 2026-09-10 | MIT | 已自我重定位为「文档 agent + OCR 平台」 | 你的问题**本质是检索/文档解析** | 通用编排不如 LangGraph/MAF |
| **Haystack** | Python | ~26.5k（estimated） | 2026（未验证） | 生产向 RAG | 检索增强、可组合 pipeline | 企业 RAG | agent 编排生态小，社区体量小 |
| **DeerFlow 2.0** | Python | 热度极高（来源口径 25k–57k，**未验证**） | 2026-02-28 发布 | MIT | Supervisor 型「超级智能体」运行时 | 长任务、研究/报告型 | **发布新、生产案例少**；别把核心合规押在它上面 |

**两句话结论**：
- **要精确控制和恢复** → LangGraph（MIT 免费；商业层是 LangSmith，$39/座/月起 + 部署按节点计费）。
- **只是顺序调几个工具** → 别用 LangGraph，用 Pydantic AI 或裸 SDK，100 行自己写循环也是正当架构。

**2026 年的格局变化（这是选型时最该知道的）**：
- **整合期已到，框架大战基本结束**：2025-10 AutoGen 进维护模式 → 微软把 AutoGen + Semantic Kernel 合并为 **Microsoft Agent Framework**，2026-04 双语言 1.0 GA，SK 只保关键修复至 ~2027-04；同一窗口 LangChain/LangGraph 1.0 GA（2025-10-22）、ADK 1.0 GA（2026-04）。
- **LangChain 系自己把定位拆成三层**：`deepagents`（长任务）/ `LangGraph`（低层可控）/ `LangChain`（快速起）。
- **厂商 SDK 上位**：OpenAI Agents SDK（29.3k，issue 数最少）、Google ADK（跨语言 + A2A）、AWS Strands（重定位为 harness，2026-06 AgentCore Harness GA）。
- **收缩或降级的**：AutoGen（2026-04 后无提交）、Semantic Kernel（停止加新功能）、LlamaIndex（重定位为文档/OCR）、smolagents（节奏放缓）。
- **实践者共识的取舍**：图驱动（LangGraph）确定性/可审计强、代码多、对模型能力依赖低；model-driven（Strands/OpenAI SDK）代码少、但对模型推理力依赖高、流程不可完全预判。**真正决定"能放多自主"的是 guardrails，不是模型准确率。**

**实测的 token 开销差异**（同模型同题，9 框架 90 次运行）：LangGraph 输入 1,288 token 最低，CrewAI 1,432 最高（约 11% 纯框架 prompt 开销，且随每次调用放大）；Claude Agent SDK 约 34,989 token/次（约 25 倍，其中大部分是打折的 cache read，实测有效成本约 4–5 倍），延迟中位数 8.5s vs 其余约 2.5s，安装 299MB（内置 Node CLI）。**Claude Agent SDK 是"全都有、你来减"，不是轻循环**——只有当你确实需要它那套 harness（文件/bash/权限/子代理/hooks）时才划算。

### 第 2 层：记忆与上下文

星数为 `api.github.com` 实测或 ungh.cc 代理（2026-09-11）。

**先记住 2026 年真正的共识：Anthropic 的公开路线是「文件系统即记忆」**——markdown 文件 + 让 agent 用 bash/grep 自己读写 + Skills 做渐进披露（progressive disclosure）。规模化之后才加四道生产护栏：**版本化 / 乐观并发（写入前后 hash 校验）/ 分层权限 / 可移植 API**；in-band 记忆的视野局限再用 out-of-band 的「dreaming」（编排器 + 子 agent 批处理会话记录，人类审批）来解。

> **对单人项目的含义：先 markdown + agent 自主写，别第一天就上记忆产品。**

| 方案 | 星数 | 许可 | 什么时候真的需要它 | 代价 |
|---|---|---|---|---|
| **mem0** | 65.1k | Apache-2.0 | 跨会话/跨用户记忆，不想自己写抽取-去重-更新管线 | 每轮额外 LLM 抽取成本；自托管要 PG+向量库+图库三件套 |
| **letta**（MemGPT 系） | 24.7k | Apache-2.0 | 愿意把「agent 运行时 + 记忆」整体交出去 | 是平台不是库，框架耦合重；`open_issues=0`、PR 仅协作者 → 社区贡献门槛高 |
| **graphiti**（Zep 系） | 30.8k | 未验证（公开称 Apache-2.0） | 需要「事实随时间演化/失效」的时间维度 | Neo4j/FalkorDB 运维 + 抽取 LLM 成本；核心在 graphiti，`getzep/zep` 只剩 4.9k 且偏示例 |
| **cognee** | 30.6k | Apache-2.0 | 想要 ETL 式记忆管线 + 图检索 | 518 open issues，API 演进快 |
| **supermemory** | 29.6k | MIT | 要 MIT + 全本地可跑 | 主栈 TS/Cloudflare，Python 生态薄 |
| **langmem** | 1.7k | 未验证（估计 MIT） | 已深度用 LangGraph/LangChain | 项目小，绑 LangChain |
| **ReMe**（原 MemoryScope） | 3.4k | Apache-2.0 | 已在 AgentScope/中文生态 | **仓库已改名迁移，旧引用会重定向失效** |
| **memobase** | 2.9k | Apache-2.0 | 角色/陪伴类要结构化 profile | **`pushed_at` 2026-01-11 ≈ 8 个月无提交，维护风险高** |

**推荐顺序**（真到了瓶颈再上）：`mem0 > cognee > letta`（letta 是整平台，最重）。

### 第 3 层：协议与工具

星数来自 `api.github.com` 实测或 ecosyste.ms/ungh.cc 镜像（同步于 2026-09-09~10）——标注来源。

| 协议 | 主导方 | 星数 | 状态 | 新项目该不该上 |
|---|---|---|---|---|
| **MCP** | Anthropic 发起 → 2025-12-09 捐给 Linux Foundation **AAIF** | 9,181 (api)；官方 servers 集 90,223 (api) | **事实标准**。1.8 万+ 服务器目录收录（2026-03）；Claude/Cursor/Copilot/Gemini/VS Code/ChatGPT 全支持 | **必上** |
| **MCP Python SDK** | 同上 | 24,265 (api)，MIT | **v2 已发布**（`pip install mcp` = 2.x） | **必上**，但先读 migration guide |
| **A2A** | Google 发起 → Linux Foundation；2026-08 成为 AAIF 项目 | 25,725 (api)，Apache-2.0 | v1.0 于 **2026-03-12** 发布（含 Signed Agent Cards）；150+ 组织、5 语言官方 SDK；Azure/Bedrock/Google 均已集成 | **留接口，别押注** |
| **ACP（Agent Client Protocol）** | Zed + JetBrains | 4,207 (api)，Apache-2.0 | 编辑器 ↔ 编码 agent；Registry 2026-01-28 上线；Claude Code/Codex/Copilot CLI/Gemini CLI/OpenCode 已入驻 | 只做编码 agent 才上 |
| **AG-UI** | CopilotKit（$27M 融资） | 15,842 (api) MIT；CopilotKit 37,291 (api) MIT | agent ↔ 前端事件协议；assistant-ui、TanStack AI 已兼容 | 自研前端时上 |
| **AGNTCY / OASF** | Cisco → Linux Foundation | 332 (api) | 协议无关的 agent 描述 schema，采用度低 | 观察，不上 |

**⚠️ MCP Python SDK v2 是破坏性改版**（这是本报告里最容易让你第一天就踩坑的一条）：
- `FastMCP` → **`MCPServer`**；字段命名 camelCase → snake_case；低层 `Server` 重写。
- 移除 WebSocket 与 experimental Tasks；`roots`/`sampling`/`logging` 弃用；`ping` 移除。
- 2026-07-28 的协议修订版转向「**无状态核心**」——取消握手/session/服务端反向请求，客户端兼容正处于过渡期。
- **生产走 Streamable HTTP，不要 stdio**；给工具加 annotations（`readOnly`/`destructive`/`idempotent`/`openWorld`），让宿主能做策略判断。

**A2A 的正确姿势**：标准赢了（v1.0、150+ 组织、LF 治理、云厂商集成），**但生产信任没跟上**——HBR 调查只有 6% 企业「完全信任」agent 管核心流程，Gartner 预计 40%+ agentic 项目在 2027 年前被砍。所以：
- **现在**：发布一个 `/.well-known/agent.json` Agent Card，把任务边界设计成「可远程委派」的形状，内部调用先用普通 HTTP/队列。
- **等第一个真实跨组织需求出现**再实现 A2A。

**ACP 命名陷阱（沟通时务必确认）**：`Agent Client Protocol`（Zed，编辑器↔agent，**活跃**）vs `Agent Communication Protocol`（IBM/BeeAI，agent↔agent，**2025-08 已并入 A2A 停止独立演进**）。同名不同物，说错会浪费一周。

**`AGENTS.md`**：已是事实标准（也是 AAIF 的创始项目之一），但**存在生态摩擦**——Claude Code 更认自己的 `CLAUDE.md`，曾因不采用 AGENTS.md 引发争议。建议：写好你的事实来源，别指望单一文件名标准。

### 第 4 层：浏览器与计算机使用

- **Browser Use**（Python，50k+★）：DOM 驱动默认选择。**每一步都是一次 LLM 调用**——新任务可以，重复工作流太贵。
- **Stagehand**（TS，MIT，v3 2026-02 重写引擎，快 44%）：需要推理的步骤用 AI，其余用脚本化 Playwright。
- **Skyvern**：视觉优先，WebVoyager 2.0 表单填写 85.85%（DOM 不可靠场景最强）。代价：常见任务比 DOM 方案低 12–17 个点、每步成本高 4–8 倍。
- **生产模式**：DOM 驱动为主路径，视觉方案作为 canvas/反机器人页面上的兜底。

### 第 5 层：编码 Agent 与沙箱

**先说最重要的判断：harness 层已经被彻底商品化。** 20 万星的 opencode 是 MIT，Codex CLI 是 Apache-2.0，OpenHands 是 MIT——**自己做 loop 没有护城河**。差异点只能建立在垂直工作流、评测集、集成面与部署形态上；真正的商业杠杆是模型/CLI 锁定。

| 项目 | 星数 | 许可 | 定位 | 备注 |
|---|---|---|---|---|
| **opencode**（原 sst/opencode → anomalyco） | 206.5k (api) | MIT | 终端 agent + TUI/桌面 | 星数最高的开源 harness；**仓库改名/迁移史混乱** |
| **openai/codex** | 123.1k (api) | Apache-2.0 | 终端 agent + 云任务 | Rust 栈，绑 OpenAI；想抄生产级 harness 看它 |
| **gemini-cli** | 106.9k (api) | Apache-2.0 | 终端 agent，**MCP 双角色** | 绑 Gemini；MCP 参考实现 |
| **OpenHands** | 87.3k (api) | MIT | Agent 平台 + 沙箱执行 | 体量大、TS 为主 |
| **Cline** | 67.8k (api) | Apache-2.0 | 编辑器内 agent + CLI + SDK | Plan/Act 分离，每步可审——**要人工审查每个动作时首选** |
| **Aider** | ~49k (est) | Apache-2.0 | 终端结对 + git 自动提交 | **上游 2026-05 后转慢**，社区已 fork `aider-ce`；只借鉴架构 |
| **Goose**（Block） | ~54k (est) | Apache-2.0 | MCP-native 终端 agent | 扩展生态绑定 |
| **SWE-agent** | ~20k (est) | MIT | 研究型 agent + ACI 设计 | 非产品化，**最适合当架构参考与评测基座** |
| **claude-agent-sdk-python** | ~8.1k (est) | MIT（SDK）/ 运行时闭源 | 包闭源 CLI 的 Python SDK | 快，但是**闭源 CLI 的外壳**，硬锁定 |
| **E2B** / **Daytona** | ~14k / ~72k (est) | Apache-2.0（Daytona 仓库未标，需核实） | 云沙箱 / 沙箱工作区基础设施 | 自建沙箱层看 Daytona，要托管看 E2B |

**如果要自己用 Python 写编码 agent，底座优先级**：
1. **OpenHands `software-agent-sdk`**（~1.1k★，很新）——官方为"用 Python 建 agent"而做的 SDK，自带沙箱/工具/会话抽象，是目前唯一把 harness 真正当产品交付的开源 Python 底座。**新，API 未稳定。**
2. **SWE-agent**——MIT、极小 Python 实现，Agent-Computer Interface 的工具粒度与反馈设计值得直接抄。
3. **Aider**——repo map 检索 + "每次改动一次 git commit" 的编辑范式值得抄；但别当长期依赖。

> **不要把闭源 CLI 当底座**：你会连带继承它的许可、ToS 与限流，且无法改核心行为。

**额外值得抄的工程取舍**：`pi`（earendil-works/pi，约 95.7k★，Flask 作者 mitsuhiko 出品，TS）——值得学的不是功能，而是两件事：① **核心极简、权限外置到沙箱**（官方给三种沙箱方案），而不是把权限管理做进框架核心导致复杂度爆炸；② **供应链防御**（依赖锁精确版本、拒绝使用发布不满 2 天的依赖、npm 生命周期脚本白名单、CI 用 `npm ci --ignore-scripts`）。近两年 npm 投毒频发，这套做法对 Python 项目同样适用（锁版本 + 审计 `setup.py`/构建后端 + 冷却期）。

#### 编码 agent 的架构共性（2025–2026 收敛出来的，自建时直接对齐）

1. **Loop**：单线程 `while`（LLM → 工具调用 → 结果回填），每轮有工具预算与显式终止条件；工具调用流式解析。
2. **工具集小而正交**：read/view、**精确字符串替换或 diff 写入（不要整文件重写）**、bash/exec、glob/grep、todo 列表、网络抓取。工具 schema 是主要扩展点。
3. **上下文管理**：repo map/索引做检索；接近上限自动压缩摘要（compaction）；读取分窗截断、工具输出截断；prompt caching。
4. **权限与审批**：per-tool `allow/deny/ask` + "always allow" 持久规则；只读 plan 模式 vs act 模式；文件访问限定工作区；命令黑白名单；危险模式需显式开关。
5. **沙箱**：容器/微虚拟机（Docker、E2B、Daytona、Firecracker）或本地工作区沙箱 + 网络出口控制 + 快照回滚。**这是安全边界的主战场。**
6. **子 agent 与并行隔离**：派生子 agent（独立上下文 + 受限工具集，只回摘要）；git worktree 隔离并行任务。
7. **MCP 双角色**：自己既是 MCP client（消费 server），也可做 MCP server（被别的 agent 调用）。
8. **会话持久化**：append-only 事件日志 + 可恢复/可分支会话 + checkpoint——长任务产品化的前提。

#### 2025–2026 新增的配置标准（新项目应对齐）

- **`AGENTS.md`**：项目指令的事实标准（注意 Claude Code 更认自己的 `CLAUDE.md`，曾有明确摩擦）。
- **`SKILL.md` / Agent Skills**：Anthropic 2025-12 开源。
- **Agent Plugins**：Vercel 2026-08-06 提出，把 `SKILL.md` + `mcp.json` 打包分发；技术委员会含 OpenAI/Amazon/Cursor/Microsoft，VS Code、Cursor、Kiro、GitHub Copilot、ChatGPT、Codex 已支持，**Claude Code 未支持**。
- **ACP**（Agent Client Protocol）：Zed/JetBrains 推动的 IDE 互操作协议，已有 Python SDK。

### 第 6 层：评估与可观测性

这层跳过是 agent 工程里**最贵的错误**。多智能体调试时间是单 agent 的 5–10 倍，没有 trace 会变成 50 倍。

| 方案 | 星数 | **许可（务必看清）** | 定位 |
|---|---|---|---|
| **Langfuse** | 34.5k | **MIT，但 `ee/`、`web/src/ee/`、`worker/src/ee/` 目录另有商业许可** | 一体化 LLM 工程平台（trace+eval+prompt 管理），OTel 原生 |
| **Opik**（Comet） | 21.9k | Apache-2.0 | trace+eval+dashboard；要 Apache 且一体化的选它。仓库 781MB，自托管组件多 |
| **Arize Phoenix** | 11.4k | ⚠️ **Elastic License 2.0（已核实 LICENSE 原文）** | 本地/notebook 友好的 trace+eval |
| **DeepEval** | 18.2k | Apache-2.0 | pytest 风格评估库——**最便宜的评估起点** |
| **promptfoo** | 25.0k | MIT | CLI/CI 优先的 prompt 回归 + 红队扫描（不是 trace 后端） |
| **OpenLLMetry** | 7.4k | Apache-2.0 | OTel instrumentation 库（GenAI semconv），无 UI |
| **Inspect AI** | — | — | 英国 AI 安全研究所，安全/越狱/PII 离线评估 |
| ~~Ragas~~ | 15.7k | 未验证 | ⚠️ **已迁移到 `vibrantlabsai/ragas`，`pushed_at` 停在 2026-02-24（约 7 个月未更新）→ 治理存疑，选型前必须确认** |

**两个最容易误判的许可坑**：
1. **Phoenix 不是 Apache-2.0，是 Elastic License 2.0**——**ELv2 禁止把软件作为托管服务提供给第三方**。常被误传为 Apache。
2. **Langfuse 的 MIT 不含 `ee/` 目录**——别把企业功能当成开源部分来规划。

**OTel 的真相**：GenAI semconv 已到 1.44.0（含 `gen_ai.*` 与 `mcp.*` 属性），但**长期处于 Development/实验级别**。结论：**用 OTel 作传输层，别把 semconv 当稳定契约**，字段改名是常态。

**建议：选框架的同时就把观测选了，别事后补。** 但**别第一天自托管 Langfuse/Phoenix**——ClickHouse+PG+Redis+S3 是第二份全职工作；先用云免费层或本地 notebook，代码里已埋好 OTel，迁移零改造。

### 第 7 层：模型与推理

| 方案 | 星数 | 定位 | 你什么时候真的需要它 |
|---|---|---|---|
| **LiteLLM** | 58.5k | AI Gateway：100+ 供应商统一 OpenAI 格式 + 成本追踪 + guardrails + 负载均衡 | **几乎所有项目都该有**：provider 抽象 + fallback + 成本可见。注意其仓库描述显示 2026 年正转向 **"Rust core with Python SDK"**（架构演进中，留意升级路径） |
| **vLLM** | 91.5k | 高吞吐 GPU 推理（PagedAttention + 连续批处理） | 有 GPU 且自建比调 API 便宜 |
| **SGLang** | 35.8k | RadixAttention 前缀缓存 + **引擎内强制结构化输出** | 已确定自建且追求极致吞吐；**与 vLLM 二选一** |
| **Ollama** | 180.6k | 本地跑量化模型，一行安装 | 开发机本地/离线调试。**不适合做单用户之外的服务端** |
| **llama.cpp** | 127.8k | CPU/边缘/量化推理，定义了 GGUF | 无 GPU 环境；不是服务化方案，要自己包一层 |

**结论**：托管 API 起步（用 LiteLLM 做统一接口与降级）。**没做过"自建 vs API 账单"的成本对比、或没有 GPU，就不要碰 vLLM/SGLang**——这是规模化后的成本问题，不是选型问题。

---

## 3. 按场景的推荐组合

| 场景 | 编排 | 工具 | 记忆 | 观测 | 备注 |
|---|---|---|---|---|---|
| **A. 通用/垂直业务 Agent（推荐默认）** | LangGraph | MCP | Postgres 起步 | Langfuse | 可控、可审计、可恢复 |
| **B. 单循环工具型助手** | Pydantic AI（或裸 SDK） | MCP | Postgres | Langfuse | 别过度设计 |
| **C. 快速验证 / 多智能体演示** | CrewAI 原型 → LangGraph 加固 | MCP | — | Langfuse（先接） | 「CrewAI 验证价值，LangGraph 做生产」是被反复验证的路径 |
| **D. 结构化数据管道 / 必须返回校验过的对象** | Pydantic AI | MCP | — | Langfuse/Logfire | 用 Pydantic 模型当契约，先定 schema 再写 prompt |
| **E. 编码 Agent** | 基于 OpenHands `software-agent-sdk`（或 SWE-agent 抄架构） | MCP + 沙箱（Docker/E2B/Daytona） | 项目级上下文 + repo map | Langfuse | 沙箱是底线不是加分项；别把闭源 CLI 当底座 |
| **F. 长任务研究型 SuperAgent** | DeerFlow 2.0 或 LangGraph supervisor 自建 | MCP + 浏览器 | 双层（working/archival） | Langfuse | 跨小时任务必须有归档 + 取回 |
| **G. 企业知识库问答（本质是 RAG）** | LlamaIndex（或 RAGFlow/Dify） | MCP | 向量库 | Langfuse/Phoenix | 先把检索质量用 gold set 调好，再包 agent |
| **H. 非工程团队/内部工具平台** | Dify / Coze Studio / n8n | 内置 | 内置 | 平台自带 | 低代码的正当场景；核心逻辑复杂到要争论框架时，你已到它的天花板 |

### 交付形态怎么改变选型（你说"不确定"，所以两种都给你）

| | 本地自用（CLI / 桌面） | 服务化（团队 / 客户） |
|---|---|---|
| 状态存储 | SQLite 或本地文件就够 | **必须 Postgres**（多租户隔离 + checkpoint + 事务） |
| 鉴权/审计 | 不需要 | 必须（谁触发了哪个动作、花了多少 token） |
| 沙箱 | Docker 或直接本地工作区（**权限外置，别把权限系统做进核心**） | 必须隔离（E2B/Daytona/Firecracker），**执行不可信代码是默认场景** |
| 观测 | Langfuse 本地 docker 单机够用 | OTel 接入你已有的 APM（Phoenix 路线） |
| 成本 | 自己盯 | 硬上限 + 配额 + 告警，多 agent 成本是单 agent 的 10–20 倍 |

**关键提醒**：Demo 快 ≠ 生产可用，但**反过来也成立**——不要为一个自己用的 CLI 工具上多租户架构。上面这条分界线（本地 vs 服务）比框架选型更早决定你的复杂度。

### 平台与 UI 层（Python 后端配什么）

| 你要什么 | 选它 | 星数 | 许可注意 |
|---|---|---|---|
| **最快出可用的 agent UI**（Python 原生） | **Chainlit** | 12.4k | Apache-2.0；2.x 有破坏性变更 |
| 自研可定制的生产级 UI | **CopilotKit** / **assistant-ui**（走 AG-UI） | 37.3k / 12.1k | 均 MIT；前端须 TS/React |
| 开箱即用的多用户聊天产品 | **LibreChat** | 42.8k | **MIT，本批许可最干净**；代价是多养 Node + MongoDB |
| 内部工具平台外壳 | **Dify** / **n8n** | 155.4k / 204.0k | Dify 禁多租户 SaaS、禁去 LOGO；n8n 是 fair-code **非 OSI 开源**。**内部用不受限** |
| 可视化编排（Python 后端） | **Langflow** | 154.6k | MIT；依赖树庞大 |
| 中文/复杂 PDF 的文档 RAG 引擎 | **RAGFlow** | 90.5k | **纯 Apache-2.0（已直读全文）——本批里唯一可放心做对外 SaaS 的平台型组件** |
| 中国生态 agent 平台 | **Coze Studio**（`coze-dev/coze-studio`，注意不是 bytedance/…） | 21.6k | Apache-2.0（媒体口径，未直读）；更新偏慢（pushed 2026-07-29） |
| 别用 | Flowise（已停服+有 CVE）、Streamlit/Gradio（长会话不友好）、Open WebUI（>50 用户品牌条款）、LobeHub（衍生分发需商业许可） | — | — |

> **注意**：`vercel/ai` 虽是 TS 数据流协议的事实标准，但**官方没有 Python SDK**（`vercel/ai-sdk-python` 已验证为 404）。Python 团队别把它当后端选型。
>
> **对外 SaaS 的许可红线**：这一批平台里，**只有 RAGFlow 是纯 Apache-2.0**。Dify / FastGPT 禁多租户、n8n / Restate 禁转售托管、Phoenix 禁对外托管——**做对外产品时，许可约束比技术选型更容易致命**。

---

## 4. 最小可用底座 → 升级触发条件 → 90 天路线

### 4.1 Day one 就上这 6 件（全部自托管、零/极低运维）

1. **LiteLLM，但只当 Python SDK 用**（先别跑 proxy 模式）——唯一的 provider 抽象层，避免被某家 SDK 绑死，顺带白拿成本/用量记录。
2. **一个 Postgres，做唯一状态源**——会话、记忆、任务队列、trace 元数据全放它。**单人项目的第二个数据库 = 第二份运维**；向量先用 `pgvector`，不要 day one 上独立向量库。
3. **结构化日志 + OpenTelemetry SDK（不装后端）**——按 `gen_ai.*` 语义埋点，后端留空。等有 UI 需求时再插 Langfuse 云免费层，此时你已零改造。
4. **评估就用 `pytest` + 20–50 条 golden case**（要 Python 断言选 DeepEval，要红队+多模型对比选 promptfoo），直接进 CI。**全表性价比最高的一项。**
5. **沙箱先用 Docker**：无网络 + 只读根 fs + `--memory/--cpus/--pids-limit` + 硬超时 + 非 root。够用到「代码来自真正不可信来源」为止。
6. **记忆先用文件系统 + markdown**（Anthropic 路线）+ 自己写 20 行 load/save；**并且自建一张 `jobs` / `job_steps` 表做 checkpoint**（或直接用 `dbos-transact-py`：MIT、一个库、只要你已有的 Postgres）。

### 4.2 明确推迟（以及推迟的理由）

| 推迟项 | 理由 |
|---|---|
| **Temporal** | 运维成本（多服务 + Cassandra/PG + 工作流代码必须确定性，禁 `Date.now()`/随机/直接 IO）远超单人项目的可靠性收益。跨小时任务先用 PG checkpoint |
| **mem0 / letta / cognee / Zep** | 每会话额外 LLM 抽取成本 + 供应商耦合。先用文件记忆跑 MVP |
| **Langfuse / Opik / Phoenix 自托管** | 组件多、运维重（Phoenix 还要注意是 ELv2，不能对外提供托管服务）。先用云免费层 |
| **Firecracker / 自研 microVM** | 自己写 VMM 编排不是产品功能。需要时买 E2B 或本地 `microsandbox` |
| **Daytona** | ⚠️ **许可证未核实**——`main` 根目录无 `LICENSE` 文件。**不清不楚的许可证别进生产依赖**；且隔离级别为 Sysbox 容器，弱于 microVM |
| **vLLM / SGLang** | 无 GPU 或没做过"自建 vs API"成本对比就不要碰 |
| **多 agent 编排框架 / 独立向量库** | 单 agent + PG + pgvector 先跑到瓶颈为止 |
| **Restate** | 技术上适合 Python durable，但 **BUSL-1.1**（4 年后转 Apache-2.0）：个人自用可以，**对外做多租户托管会触发商业授权** |

### 4.3 什么时候必须升级（可量化的触发条件）

| # | 触发条件 | 必须加的东西 |
|---|---|---|
| 1 | 单次任务 **> 5 分钟**，或"重跑一次的成本 > 你半小时调试时间" | 持久化执行：先自建 PG checkpoint / `dbos-transact-py`；跨天 + 多变体 + 要审计 → Temporal |
| 2 | **副作用不可重复**（发邮件、付款、写外部系统） | durable execution + **幂等键**。这是唯一不能靠"重试"解决的问题，**优先级最高** |
| 3 | **多租户**（数据隔离、按租户限额/审计） | 记忆层开始划算 + 分层权限模型。**注意这一刻 ELv2/BUSL 类许可风险被放大**（你成了对外服务提供方） |
| 4 | **审计合规**：要能回答"上周三这个 agent 为什么这么做" | trace 后端 + **记忆版本化与回滚**（谁改的、基于哪次会话改的） |
| 5 | **工具数 > 15–20**，或 prompt 每周都在改 | 系统化 eval：golden set 扩到 100+、加 LLM judge 并做人工校准、CI 卡回归 |
| 6 | **执行代码来自不可信来源**（用户提交 / 模型自由生成 + 有外网） | microVM 级隔离：E2B（托管）或 microsandbox（本地）。Docker 到此为止 |
| 7 | **>1 个 agent 并发写共享记忆** | 乐观并发控制（写前写后 hash 比对）+ 版本化 + 分层权限 |
| 8 | **需要多 provider 路由**（成本优化 / fallback / 地区合规） | LiteLLM 从 SDK 升级为 **proxy 模式** |
| 9 | **自建推理的成本模型成立** | vLLM **或** SGLang（二选一），前面配 LiteLLM 路由 |

> **一条贯穿始终的原则**：把「昂贵或有副作用的步骤 = 一个 checkpoint」**从第一天就写进代码结构**（一个 PG 表就够）。这样后面无论换 DBOS、Restate 还是 Temporal，都只是换执行器，而不是重写业务逻辑。
>
> **顺带一个 2026 年的认知更新**：durable execution 已分成两条路线——Temporal 式**确定性重放**（保证更强，但框架要接管你的代码）vs 新的 **checkpoint-based replay**（只保证"step 结果可复用"，step 之间的代码可以重跑、可以非确定）。对单人 Python 项目，后一种心智模型更贴合。

### 4.4 90 天时间线

**第 1 周：把"工具"做成资产**
- 用 **MCP Python SDK v2** 写 2–3 个真实工具（**先读 migration guide**，v2 对 v1 是破坏性改版），先不考虑 agent。
- 建 `AGENTS.md` / `SKILL.md` 写清项目事实与工作流（这部分换框架不用重写）。

**第 2–3 周：最小可用 agent**
- 用 **Pydantic AI**（或裸 SDK）跑通单 agent + 你的 MCP 工具，输出用 Pydantic 模型约束。
- 接 OTel 埋点；先把 `pytest` + golden case 跑起来。
- UI 想要快：**Chainlit**（Python 原生 + Apache-2.0，pip 装完即用）。

**第 4–8 周：按触发条件升级**（见 4.3，而不是按日历）
- 出现跨分钟任务 / 人工审批 / 并行分支需确定性合并 / 崩了不能从头跑 → 迁 **LangGraph**。
- Postgres checkpointer；把状态 schema 当 API 设计（reducer 决定并行写入怎么合并：`operator.add` = 追加，默认 = 覆盖，**并行场景不设 reducer 必崩**）。

**第 9–12 周：加固**
- 评估集进 CI；trace 后端按需接入。
- 成本护栏：单任务 token/工具调用硬上限（Pydantic AI 原生支持；多 agent 成本可达单 agent 的 10–20 倍，5 个 agent 一次任务可能触发 50–100 次 LLM 调用）。
- 权限与沙箱：危险动作白名单化，**"依赖模型配合才成立的控制"不是控制**。

---

## 5. 2026 年特有的避坑清单

1. **教程/榜单严重过期**。大量"2026 最佳框架"仍在推荐 2025 年初就废弃的 OpenAI Swarm、不提 Pydantic AI/Claude Agent SDK、也不提 AutoGen 已进维护模式。**看发布日期，看是否提到 Agent Skills/MCP/A2A**。
2. **星数和下载量都不是采用度**。10M 月下载里包含 CI 每次重装；54.5k 星的 AutoGen 是历史存量；"60% Fortune 500 采用"可能只是一个业务部门在笔记本上跑过 demo。**看有没有可审计、产生营收的生产部署**。
3. **别用多智能体解决单智能体的问题**。多 agent 带来 3–5 倍 token 与 5–10 倍调试成本，多数"协作"其实是可预测的工作流，用图和分支就好。
4. **别把 token 预算当无限**。压缩、缓存感知（改动一个字符就击穿 prompt cache）、重复工具输出归并，这些是 2026 年的工程常识。
5. **Skills/MCP 是供应链攻击面**。装第三方技能=执行未审计代码；MCP server 同理。自建 + 审计。
6. **别迷信"一个生态全包"**。七层各选最优，接缝很薄；试图用一个生态满足全部约束，结果是每层都平庸。
7. **框架 API 漂移是常态**。9 个框架实测里 5 个在写第一行业务代码前就撞上 API 漂移/废弃/安装失败。**锁版本、读 changelog**。

---

## 6. 反选清单（明确不要选什么）

| 不要选 | 原因 |
|---|---|
| **AutoGen**（原仓库）新项目 | 维护模式，**最后 push 停在 2026-04-15（近 5 个月无提交）**；名字指向三处；根 LICENSE 是 CC-BY-4.0（代码许可不明）。要看微软系就看 `microsoft/agent-framework` |
| **Flowise** | ⚠️ **已归档停服**（`archived=true`，last push 2026-08-13，官网已挂 sunset 页），且有 CVE-2026-71962（未授权文件泄露）+ vm2 沙箱逃逸 RCE |
| CrewAI 自主 crew 上生产 | 无 checkpoint、token 消耗高、委派可能循环；用 Flows |
| 用 Claude Agent SDK 搭"轻量通用 agent" | 3.5 万 token/次、8.5s 延迟、299MB、锁模型——只有需要整套 harness 才值 |
| **Dify / FastGPT 当对外多租户 SaaS 底座** | 许可明文禁止运营同类多租户服务（需书面授权）+ 禁去 LOGO。**内部用没问题** |
| **n8n / Restate 对外托管** | n8n = Sustainable Use License（fair-code，非 OSI）；Restate = BUSL-1.1——**都禁止把软件本身作为商业服务转售/托管** |
| **Phoenix 对外提供托管服务** | Elastic License 2.0 明文禁止（常被误传为 Apache-2.0） |
| **Daytona 进生产依赖** | 根目录无 `LICENSE` 文件，许可不明；隔离级别也只是 Sysbox 容器 |
| **Ragas / memobase** | 前者已迁到 `vibrantlabsai/ragas` 且 7 个月未更新，后者 8 个月无提交——治理存疑 |
| **Streamlit / Gradio 做长会话 agent UI** | 每次交互整脚本重跑，长会话与流式不友好 → 只适合 demo。agent UI 用 Chainlit（Python）或 AG-UI 系 |
| **Open WebUI 用于 >50 终端用户** | 品牌条款：30 天内超 50 终端用户不得移除品牌，否则需企业授权 |
| 第一天就上向量库 + 记忆框架 | 先 Postgres + 文件记忆，触发条件到了再上 |
| 把订阅账号当 API 网关（OmniRoute 这类玩法） | 违反多数供应商 ToS，个人实验与团队共享是两种法律处境 |
| 盲抄 Skills 包 | 它能以你的权限执行代码 |

---

## 7. 需要你自己回答的三个问题（决定最终选型）

1. **任务时长**：单次交互秒级/分钟级，还是跨小时跨天？（决定要不要 checkpoint 与 durable execution）
2. **谁能批准危险动作**：人在环是核心流程还是事后补？（决定编排框架，LangGraph 在这点上领先）
3. **锁定容忍度**：能否接受绑一家模型厂商或一家云？（决定用厂商 SDK 还是中立框架）

> 我的默认赌注：**Python + LangGraph + MCP + 一个 Postgres + OTel 埋点**，原型阶段允许用 CrewAI/Pydantic AI 抄近路，UI 用 Chainlit。这个组合在 2026 年有最多的公开生产案例，且 **MCP 那层投资不会因为换框架而作废**——而许可陷阱（ELv2/BUSL/fair-code/品牌条款）往往比技术选型更早杀死一个对外产品，所以先在 6. 反选清单里划掉红线。

---

## 附 B：怎么自己复核这些数字（星数、活跃度、许可）

**星数和下载量会骗人，活跃度和许可不会。** 复核任何一个仓库，四个字段就够：

```bash
# 无需 gh CLI，直接打 API（匿名 60 次/小时，出口 IP 共享，省着用）
curl -s https://api.github.com/repos/langchain-ai/langgraph \
  | python -c "import json,sys; d=json.load(sys.stdin); print(d['full_name'], d['stargazers_count'], d['pushed_at'], (d.get('license') or {}).get('spdx_id'), d['archived'])"
```

看四件事：
1. **`pushed_at`**——比星数重要得多。星数是历史存量（AutoGen 60.9k 星但 2026-04 后无提交）。
2. **`open_issues_count`**——triage 健不健康（OpenAI Agents SDK 只有 38 个未关闭 issue，是同类里最健康的）。
3. **`license.spdx_id`**——注意 GitHub 自动识别值可能与社区印象不符（AutoGen 被识别成 `cc-by-4.0`，以仓库 LICENSE 文件为准）。
4. **`archived`** 与仓库改名（`strands-agents/sdk-python` → `harness-sdk`、`sst/opencode` → `anomalyco/opencode`：API 会 301，旧教程链接会静默失效）。

**更省事的办法**：装 `gh` CLI（`winget install --id GitHub.cli`）+ 本会话可用的 `github-explore` 技能脚本，一条命令就能拿到过滤掉 fork/archived 的结果。

---

## 附 C：主要参考来源

- [The 9 Best AI Agent Frameworks in 2026（9 框架实测，2026-07）](https://www.agentmail.to/blog/best-ai-agent-frameworks-2026) — 安装体积、token 开销、延迟实测
- [Open-Source Agent Framework Market Share 2026（2026-04）](https://agentmarketcap.ai/blog/2026/04/08/open-source-agent-framework-market-share-2026-langraph-crewai-autogen) — 生产部署 vs 星数/下载量
- [2026 年 8 月 GitHub 热榜深度拆解：Agent Skills 席卷开源圈](https://juejin.cn/post/7676748940241633307) — Skills 生态、记忆/上下文、pi、buzz、OmniRoute
- [2026 年开源 Agent 工具包选型指南：七层栈](https://developer.aliyun.com/article/1740959) — 分层选型、替换成本、开源 vs 开放核心
- [选错框架等于白干——AI Agent 框架终极对决（2026-06）](https://cloud.tencent.cn/developer/article/2698045) — 状态管理/故障恢复/可观测性横向对比
- [Microsoft Agent Framework Version 1.0（2026-04-03 官方）](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/) — 合并 AutoGen + Semantic Kernel，.NET 与 Python 同时 1.0
- [A2A Protocol Surpasses 150 Organizations（Linux Foundation）](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)
- [Claude + MCP in 2026: From Anthropic Side Project to Linux Foundation Standard](https://webbitech.com/blog-details/claude-mcp-in-2026-from-anthropic-side-project-to-linux-foundation-standard)
- [Awesome AI Agent Frameworks（2025-09 版本，含选型取舍）](https://github.com/axioma-ai-labs/awesome-ai-agent-frameworks)
- GitHub API 实测：`openai/openai-agents-python` = 29,337★ / MIT / 最后推送 2026-09-10（2026-09-11 查询）
- 编排框架批量实测（2026-09-11，`api.github.com`）：[langgraph](https://api.github.com/repos/langchain-ai/langgraph) 41.4k / [crewAI](https://api.github.com/repos/crewAIInc/crewAI) 58.4k / [langchain](https://api.github.com/repos/langchain-ai/langchain) 146.1k / [autogen](https://api.github.com/repos/microsoft/autogen) 60.9k（2026-04-15 后无提交）/ [agent-framework](https://api.github.com/repos/microsoft/agent-framework) 13.5k / [adk-python](https://api.github.com/repos/google/adk-python) 21.5k / [pydantic-ai](https://api.github.com/repos/pydantic/pydantic-ai) 19.9k / [smolagents](https://api.github.com/repos/huggingface/smolagents) 29.3k / [llama_index](https://api.github.com/repos/run-llama/llama_index) 52.1k
- [LangChain / LangGraph 1.0 发布（2025-10-22）](https://www.langchain.com/blog/langchain-langgraph-1dot0) 与 [LangChain vs. AutoGen（2026）](https://www.langchain.com/resources/langchain-vs-autogen)
- [Strands Agents 已改名 harness-sdk、AgentCore Harness GA（Zenn, 2026-08-27）](https://zenn.dev/ino_h/articles/2026-08-27-strands-agents-harness-value) / [Google ADK 1.0 + A2A（DEV）](https://dev.to/x4nent/google-adk-10-a2a-protocol-pythongojavatypescript-ga-agentcard-task-and-sse-redefining-58e5)
- 编码 Agent 实测（2026-09-11）：[OpenHands](https://api.github.com/repos/All-Hands-AI/OpenHands) 87,279★ / [opencode](https://api.github.com/repos/sst/opencode) 206,513★ / [codex](https://api.github.com/repos/openai/codex) 123,145★ / [gemini-cli](https://api.github.com/repos/google-gemini/gemini-cli) 106,904★ / [cline](https://api.github.com/repos/cline/cline) 67,807★
- [Agent Plugins 打包规范（2026-08）](https://www.itmedia.co.jp/aiplus/article/2608/10/2000000487/) / [Agent Plugins spec 仓库](https://github.com/agentplugins/agent-plugins-spec) / [JetBrains × Zed 的 ACP 互操作](https://blog.jetbrains.com/ai/2025/10/jetbrains-zed-open-interoperability-for-ai-coding-agents-in-your-ide/)
- **协议治理**：[Linux Foundation 成立 AAIF（2025-12-09，MCP/goose/AGENTS.md 为创始项目）](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation) · [A2A v1.0 + 加入 AAIF](https://a2a-protocol.org/latest/blog/archive/2026/) · [IBM ACP 并入 A2A（2025-08-29）](https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/) · [MCP Python SDK v2 变更](https://py.sdk.modelcontextprotocol.io/whats-new/) · [Zed ACP Registry（2026-01-28）](https://zed.dev/blog/acp-registry) · [A2A 一周年批判性评估](https://vibeagentmaking.com/blog/a2a-at-one-year-the-standard-won-and-nobody-has-production-trust/)
- **许可条款直读**：[Dify LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE) · [FastGPT LICENSE](https://github.com/labring/FastGPT/blob/main/LICENSE) · [n8n 许可文档](https://docs.n8n.io/n8n-community-license) · [Restate LICENSE（BUSL-1.1）](https://cdn.jsdelivr.net/gh/restatedev/restate@main/LICENSE) · [Arize Phoenix LICENSE（ELv2）](https://cdn.jsdelivr.net/gh/Arize-ai/phoenix@main/LICENSE) · [Open WebUI LICENSE（品牌条款）](https://cdn.jsdelivr.net/gh/open-webui/open-webui@main/LICENSE) · [Flowise 停服公告](https://flowiseai.com/sunset)
- **记忆/持久化执行**：[ZenML：Checkpoint Replay 与 Durable Execution 走向（2026-05）](https://www.zenml.io/blog/where-durable-execution-is-headed) · [ZenML LLMOps DB：Anthropic 的上下文工程与记忆管理](https://www.zenml.io/llmops-database/context-engineering-and-memory-management-for-production-agent-systems) · [Temporal：Doubling down on AI（2026-04）](https://temporal.io/blog/doubling-down-on-ai)
- **沙箱**：[Modal：2026 年 AI 代码执行 microVM 沙箱横评](https://modal.com/resources/best-microvm-sandboxes-ai-code-execution)（厂商自述，需打折） · [OpenTelemetry GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/)（注意仍是 Development 级）
- 记忆/观测/沙箱/持久化执行层的星数与许可实测：`api.github.com/repos/{mem0ai/mem0, letta-ai/letta, getzep/graphiti, topoteretes/cognee, langfuse/langfuse, Arize-ai/phoenix, comet-ml/opik, confident-ai/deepeval, promptfoo/promptfoo, e2b-dev/E2B, daytonaio/daytona, temporalio/temporal, restatedev/restate, dbos-inc/dbos-transact-py, BerriAI/litellm, vllm-project/vllm, sgl-project/sglang}`（2026-09-11，部分经 `ungh.cc` 代理）
