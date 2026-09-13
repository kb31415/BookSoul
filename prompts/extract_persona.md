<!--
Prompt 3：逐章 persona 增量抽取（🔴 核心）

来源：`PROMPT_DESIGN.md` §5，**原文照搬**（实现方不改写设计）。
变量用 `{name}` 占位，由 `booksoul.extract.persona` 用 str.format 填充。

变量表（§5）：
  {character_name}   阶段 3 归并后的主名
  {aliases}          阶段 3 归并结果（用别名一起检索能显著提高召回）
  {chapter_index}    阶段 2 章号
  {chapter_title}    阶段 2 章节标题
  {existing_persona} 前几章的合并结果（见 §7）；第一章为空
  {relevant_passages} 用主名 + 所有别名做关键词检索，取该章内命中的段落（≤5000 字）
-->
你是文学人物分析师。你的任务是从小说文本中，为角色构建立体的人物画像。

【角色】{character_name}
【别名】该角色在本书中也可能被称为：{aliases}
【当前章节】第 {chapter_index} 章 {chapter_title}

【已有画像】
{existing_persona}
（如果是第一次抽取，此处写"（无，这是首次抽取）"）

【任务】
从下面的章节文本中，抽取关于「{character_name}」的**本章新增或改变**的信息。

【字段定义】
- personality（性格）：15–40 字。**抓住最独特的矛盾点或行为模式**，不要堆砌形容词。
  ❌ 差例："性格复杂，既有温柔的一面也有冷漠的一面"（任何角色都能套）
  ✅ 好例："表面疏离寡言，对在意的人用行动而非言语表达"
- desire（欲望）：10–25 字。他想要什么？**具体到事**。
  ❌ 差例："希望得到幸福"
  ✅ 好例："想护她周全，却不肯承认这份在意"
- flaw（缺陷）：10–25 字。性格弱点或行为惯性。
  ❌ 差例："有时不够果断"
  ✅ 好例："过度隐忍，宁可被误解也不解释"
- secret（秘密）：10–30 字。不轻易示人的事。
  ❌ 差例："他有自己的秘密"
  ✅ 好例："他的真实身份是前朝遗孤（第12章揭示）"
- speech_style（说话风格）：10–25 字。用词、语气、句式特点。
- relationships（关系变化）：本章中他与谁的关系发生了什么变化。

【质量约束】（违反则输出无效）
1. **每条结论必须附原文引用**（写入 quotes）。**没有原文支撑的字段，宁可输出空字符串**，绝不编造。
2. **禁止通用套话**：不得使用"性格复杂""内心矛盾""很有魅力""多面性"这类空泛表述。
3. **标注置信度**：quotes 中每条用 confidence 标注 —— "explicit"（文中明说）/ "inferred"（你的推断）。
4. **增量原则**：已有画像中已包含的信息不要重复输出；只输出本章新增或与已有不同的部分。
5. **冲突处理**：若本章信息与已有画像矛盾（如性格转变），写入 changes 字段并给出原文依据，**不要静默覆盖**。

【输出格式】严格输出以下 JSON，不要任何额外文字、不要 markdown 代码块标记：
{
  "personality": "",
  "desire": "",
  "flaw": "",
  "secret": "",
  "speech_style": "",
  "relationships": [{"target": "", "change": ""}],
  "quotes": [
    {"field": "personality", "text": "原文引用原文", "confidence": "explicit"}
  ],
  "speech_samples": ["本章中该角色最能体现说话风格的一句原话"],
  "changes": [{"field": "", "from": "", "to": "", "reason": "原文依据"}]
}

【章节文本中与「{character_name}」相关的段落】
{relevant_passages}
