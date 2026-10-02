# SillyTavern 记忆与提示词体系调研 → HyPRA 设计参照

> 目的：为 HyPRA 的「提示词架构 + 混合记忆引擎」提供经过验证的参照机制。
> 调研方式：SillyTavern 官方文档（SillyTavern-Docs / docs.sillytavern.app）+ 社区插件 GitHub 仓库。
> 结论：只借鉴「架构思想与交互机制」，不复制任何代码；本项目代码 100% 原创。

---

## 一、SillyTavern 官方机制（直接参照）

### 1.1 Prompt 最终组装顺序（官方）

单次请求的上下文按以下顺序拼装：

1. Main instructions（主指令 / 人设系统提示）
2. 角色卡定义（AI 扮演的角色）
3. persona（用户扮演的角色）
4. **World Info 命中条目**（世界信息）
5. **Data Bank 检索文档**（向量 RAG）
6. **Summaries**（过往对话摘要）
7. Web 搜索 / 外部工具结果（function calling）
8. 之前的对话消息
9. 用户本次消息
10. 尾部指令（post-history instructions）

→ **设计启示**：注入内容要分级定位（人设→世界→记忆→摘要→对话），不是一股脑拼在开头。

### 1.2 World Info（世界书）机制

- 本质：**动态字典**。只有条目关键词在对话文本中出现时，才把该条目内容注入 prompt。
- 触发：关键词列表（默认不区分大小写、可配置正则/大小写）、`trigger%`（概率）、字符过滤、分组（inclusion group）；
  另有**向量触发**：条目标 Vectorized 后可由向量检索命中（替代关键词判定）。
- 条目注入位置可选，影响权重：
  - Before/After Char Defs：角色定义前后（中等影响 / 更大影响）
  - Before/After Example Messages：作为示例对话块
  - Top/Bottom of AN：Author's Note 顶部/底部
  - **@ Depth N**：插入聊天深处指定位置（Depth 0 = prompt 底部），可选 system/user/assistant 角色消息
  - **Outlet**：不自动注入，用 `{{outlet::Name}}` 宏在任意位置显式调用
- 预算：激活设置里可按 token 预算限制命中条目总数。

→ **设计启示**：世界书 = "规则 + 关键场景 + 人物背景"的关键词/正则/向量三重触发 + 注入深度分级控制。

### 1.3 Data Bank / Vector Storage（官方 RAG 参考实现）

- 官方 Vector Storage 是内置参考实现：文件 Embedding 后入库，生成时用向量检索最相关 chunk 注入 prompt。
  - 存储细节：用 Vectra 库，向量存 **JSON 文件**（`/vectors` 目录，每文档一个 collection）→ 官方实现偏轻量，海量数据不是强项。
  - **HyPRA 不用官方存储，选 Qdrant（见 3.4）**，但借鉴其分块与注入参数。
- 分块参数：chunk size（字符）、overlap（相邻块重叠 %）、size threshold（超过才分块）、retrieve chunks（跨文件共享的检索上限）。
- 注入规则：检索到的多个同文件 chunk 按原文顺序插入；注入模板可用 `{{text}}` 宏定位文本位置；注入位置规则同 World Info/Author's Note。
- 多 chunk 会**在塞入聊天消息之前预留一块上下文**。

→ **设计启示**：向量召回参数 = 分块大小 + 重叠 + 检索上限 + 注入位置 + 注入模板（可带自定义包装指令）。

### 1.4 Chat Vectorization（聊天向量化）

- 把**当前会话内的历史消息**做向量检索，把与最近消息最相关的旧消息**临时挪到上下文开头或结尾**（只在本次生成时生效，不改变存储顺序）。
- 关键价值：找回**久远到已超出 context 窗口、但对当前话题相关的消息**。

→ **设计启示**：热层滚动窗口之外的"漏网"相关消息，可用会话内向量化补回 —— 这正是 100+ 轮长对话防遗忘的手段之一。

### 1.5 Summarize（摘要扩展）

- 按**消息数间隔自动触发**（update every X messages，0 = 手动）；也可手动 Summarize now。
- 摘要 prompt 可配置，支持 `{{words}}` 宏指定目标字数；生成用独立 API 长度设置。
- 官方提醒：摘要是 LLM 生成物，**可能丢失细节**，只能算近似长期记忆。

→ **设计启示**：摘要要定时 + 可手动兜底；**别把摘要当唯一长期记忆**，要与向量/结构化记忆互补。

> **本项目取舍**：HyPRA **未采用**摘要机制。评估结论——本项目若只做「增量并入」
> 而不做压缩，摘要会退化为工作记忆窗口的降级副本（同样在记录「最近说了什么」，
> 却丢了原句），反而挤占 token 预算；而真正的压缩需要额外 LLM 调用。
> 更清晰的分工是：情景记忆（混合检索）答「具体发生过什么」，
> 语义记忆（结构化事实）答「稳定的属性是什么」，两者叠加已覆盖摘要的用途。
> 详细理由与取舍过程见 [`memory-architecture.md`](memory-architecture.md) 附录 P7。

---

## 二、社区插件已验证的设计（择要）

### 2.1 Smart Memory（senjinthedragon）—— 分层记忆 + 激活词加权

- **三层常驻记忆 + 官方向量检索**，明确互补而非替代：

| 层 | 内容 | 生命周期 |
|---|---|---|
| long-term | 角色核心事实（跨会话） | 永久 |
| short-term | 对话摘要（窗口之前的叙事概览） | 随会话滚动 |
| session | 本次会话细节（场景、关系变化、物品地点） | 仅当次会话 |
| vector storage | 具体细节按需语义召回 | 检索触发 |

- 每个记忆带**自动生成的激活触发词**：当前轮出现该词 → 该记忆得分提升、排到注入前列，且放到**更靠近 prompt 的位置**（生成前可见）。
- 摘要**增量合并**：第一份摘要写完后，只把新消息并入，不每次整段重写；且知晓 long-term 已有内容，避免重复。
- session 层感知 long-term 已有内容 → 互补不重复。

→ **设计启示**：HyPRA 的记忆层职责要这样切分；"激活词加权 + 靠近 prompt 摆放"可做情绪/话题联动。

### 2.2 VectHare（Coneja-Chibi）—— 时间衰减 + 条件激活 RAG

- 对整段聊天历史做语义检索召回；**temporal decay**：旧记忆随年龄自然衰减权重，避免陈年闲聊盖过近期主线。
- **条件激活**：用规则控制"何时才触发记忆检索"（不是每轮都搜）。
- 多向量后端（其中含 Qdrant）。

### 2.3 VectFox（KritBlade）—— 服务端事件驱动检索

- 检索逻辑全在**服务端向量库**内完成；用**结构化事件**取代"整段 chunk 再摘要"的粗糙做法，召回更准；2000+ 条消息查询 < 3s。

### 2.4 CharMemory（bal-spec）—— 结构化记忆抽取

- 自动从聊天中抽取**结构化记忆（实体/关系/事实）**写入 Data Bank，生成时由向量检索召回。
- 与 HyPRA 冷层"回复后 LLM 自动抽取时间/地点/人物关系"同思路（社区已验证）。

### 2.5 Qdrant 系社区插件

- **ST Qdrant Memory（HO-git）**：对话自动入库 Qdrant + 生成时语义召回；**每个角色独立 collection** 实现记忆隔离。
- **RAG for SillyTavern（gardianofthedarkness）**：Qdrant（Docker 初始化）+ OpenAI embedding + 角色记忆持久化。
- 结论：**Qdrant 是该生态里被反复选用的向量库**（官方参考实现太轻，社区都换 Qdrant）。

---

## 三、沉淀：HyPRA 可落地的 8 条参照

1. **Prompt 组装顺序固定化**：人设 → 世界书命中 → 向量召回 → 结构化事实 → 滚动窗口 → 本次输入（**本项目不采用摘要层**，见 §1.5）。实现为 LangGraph 的 PromptManager 节点，输出前做 token 预算裁剪。
2. **世界书三重触发**：关键词（默认）+ 正则 + 向量；注入位置分档（角色定义附近 / 聊天深处 @N / 宏定位），可配 token 上限。
3. **记忆写入时机**：回复完成后**事件驱动抽取**（结构化事实；「摘要增量并入」一项**本项目未采用**，见 §1.5），不是定时全量重扫。
4. **摘要滚动增量**：首份摘要后只并入新消息；摘要知晓冷层已有事实，避免重复占用。**本项目未采用**——见 §1.5 与 `memory-architecture.md` 附录 P7。
5. **激活词/情绪加权**：当前轮命中某记忆关键词或情绪标签 → 提升该记忆注入优先级并靠近 prompt。
6. **时间衰减**：向量召回按时间衰减重排，旧记忆让位于近期主线（100+ 轮测试时可调参数验证防幻觉）。
7. **分层职责**：长期核心事实常驻（防人设崩塌锚点）、会话细节仅本会话、向量按需补细节、滚动窗口保即时感 —— 四者互补不重复。
8. **隔离与扩展**：每个"陪伴对象"（角色）独立 Qdrant collection + 独立冷层命名空间；接入层做成接口，便于换 embedding / 后端。

### 对应到 HyPRA 四层

> 本节沿用「热 / 温 / 冷」这一传统分层说法，与 `memory-architecture.md` 的四层对应关系如下：
> **温层 = 情景记忆**，**冷层 = 语义记忆**；个人记忆与情景记忆共用 Qdrant 底座，独立 collection。
> 注意：**本项目未采用摘要层**（见 §1.5 与 `memory-architecture.md` 附录 P7）。

| HyPRA 层 | 借鉴来源 | 载体 |
|---|---|---|
| 工作记忆（热，最近 N 轮） | 官方滚动窗口 + Chat Vectorization 会话内补漏 | 内存 |
| 情景记忆（温） | Data Bank/Vector Storage 参数 + VectHare 衰减 + Qdrant 社区插件 | **Qdrant**（云/本地 Docker） |
| 语义记忆（冷） | CharMemory / Smart Memory 自动结构化抽取 | SQLite 结构化事实表 |
| 个人记忆 | Data Bank 分块 / 注入参数 | Qdrant（独立 collection，无衰减） |

---

## 四、来源清单

- SillyTavern 官方文档（SillyTavern-Docs, main 分支）：
  - Usage/Prompts/index.md、Usage/worldinfo.md、Usage/core-concepts/data-bank.md、extensions/Summarize.md、extensions/Chat-vectorization.md、extensions/index.md
- docs.sillytavern.app：Data Bank (RAG)、World Info、Chat Vectorization、Summarize 页面
- 社区插件仓库：
  - senjinthedragon/Smart-Memory（AGPL-3.0）
  - Coneja-Chibi/VectHare（MIT）
  - KritBlade/VectFox
  - bal-spec/sillytavern-character-memory
  - HO-git/st-qdrant-memory
  - gardianofthedarkness/RAG_for_sillytavern

> 合规提醒：以上社区插件多为 AGPL-3.0，**只借鉴机制思想，不复制代码/提示词原文**；
> 本项目参照文档第 2.3 条均改为自写实现。
