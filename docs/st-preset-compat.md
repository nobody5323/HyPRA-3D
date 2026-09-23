# SillyTavern 预设兼容契约（P0 · 冻结稿）

> 状态：**P0 契约已确认；P1–P7 均已实现**（解析 / 组装 / 宏 / 后端接入 / 前端面板与条目编辑器）。
> 实现进度见 §14。
> 目的：让酒馆（SillyTavern，下称 ST）用户把已有的 **Chat Completion 预设 JSON** 导入 HyPRA 后，
> 提示词组装按 ST 语义生效，而不只是提取采样参数。
> 原则：**借鉴机制思想、自写实现**（对齐 AGENTS.md §6）；本文档不含任何 ST 或社区预设的提示词原文。
>
> **配套功能**：预设导入后的「一键 AI 适配」（改成陪伴对话形态）见
> [`docs/preset-ai-adaptation.md`](./preset-ai-adaptation.md)。

## 0. 一句话范围

兼容 = **完整解析 ST 预设字段** + **复现 ST 的组装语义**（含 marker 占位、Relative/In-Chat 注入、
条目开关与排序、生成类型触发、扩展注入）+ **采样参数透传**。
不含 ST 的脚本/扩展执行能力（见 §8 不支持清单）。

---

## 1. 合规边界（红线，先讲清）

| 类别 | 内容 | 处理 |
|---|---|---|
| **发行内容** | 仓库源码、内置预设、镜像、文档 | **零** ST／社区提示词原文。内置预设继续全部自创 |
| **运行时用户数据** | 用户在自己机器上导入的 ST 预设 JSON（含社区预设正文） | 存 `backend/data/presets/`（`.gitignore` 已覆盖 `backend/data/`），**不入库、不随发行物分发** |

三点纪律：

1. 不内置 ST 官方默认模板文本（`Default.json` 里的 main / nsfw / jailbreak 正文属 ST 项目内容）。
   §5 映射表只描述 **marker 语义**，原文一律由用户导入的预设提供；用户未导入时该槽位为空。
2. 测试夹具只用**自造的**脱敏样例 JSON（结构真实、正文自写），不引入真实社区预设文件。
3. 导入的预设文件在界面上明确标注来源为"用户本地导入"，导出功能原样导出用户自己的内容。

---

## 2. ST 预设的两层结构

ST 预设 JSON（顶层对象）可拆成两半：

```
{
  "temperature": 1, "top_p": 1, "openai_max_tokens": 300, ...   ← ① 采样参数（扁平字段）
  "prompts": [ {...}, {...} ],                                  ← ② 提示词条目定义
  "prompt_order": [ { "character_id": 100000, "order": [ {"identifier": "main", "enabled": true}, ... ] } ]
}
```

- **① 采样参数**：扁平字段，直接可用（本项目已部分支持，见 §4）。
- **② 提示词条目**：`prompts[]` 是**定义表**（id → 正文/角色/注入方式），
  `prompt_order[]` 是**启用顺序表**（每个角色一份，`enabled` 开关 + 排列顺序）。
  真正的发送内容 = 用 `prompt_order` 的顺序去 `prompts` 里取条目。

> 依据：`public/scripts/PromptManager.js`（`Prompt` 类与 `PromptCollection`）、
> `default/content/presets/openai/Default.json`（默认结构与 marker 清单）。

---

## 3. 条目字段（`prompts[]` 单项）

| 字段 | 类型 | 语义 | 本项目处理 |
|---|---|---|---|
| `identifier` | string | 条目唯一 id；marker 靠它识别（如 `main`） | 原样保留 |
| `name` | string | 界面展示名，不发给模型 | 原样保留 |
| `content` | string | 正文；marker 项为空 | 原样保留（用户本地数据） |
| `role` | `system`/`user`/`assistant` | 该条目的消息角色 | 原样透传 |
| `system_prompt` | bool | 是否为「系统提示类」条目（受 System Prompt 覆盖与合并逻辑影响，见 §6.4） | 原样保留并参与组装 |
| `marker` | bool | 占位标记条目（正文由运行时填充，不可编辑正文） | 原样保留；按 §5 填充 |
| `injection_position` | `0`/`1` | `0`=RELATIVE（跟着 `prompt_order` 走）；`1`=ABSOLUTE（**In-Chat**，按 depth 插进历史） | 完整实现 |
| `injection_depth` | int | 仅 ABSOLUTE 生效：`0`=最后一条消息**之后**，`1`=最后一条**之前**，以此类推 | 完整实现 |
| `injection_order` | int | 仅 ABSOLUTE 生效：同 depth 内排序，**越小越靠前**；缺省 `100` | 完整实现 |
| `injection_trigger` | string[] | 生成类型白名单，空 = 全部类型。取值：`normal` `continue` `impersonate` `swipe` `regenerate` `quiet` | 见 §6.5 |
| `forbid_overrides` | bool | 禁止被 System Prompt / 扩展覆盖 | 保留字段；按 §6.4 处理 |
| `extension` | bool | 由扩展注入（非用户条目） | 保留；用户导入的预设一般不含 |
| `position` | string/number | 历史遗留字段 | 保留但忽略 |

常量：`DEFAULT_DEPTH = 4`、`DEFAULT_ORDER = 100`（字段缺失时的缺省值）。

---

## 4. 采样参数字段

| ST 字段 | 本项目字段 | 处理 |
|---|---|---|
| `temperature` | `temperature` | 透传 |
| `top_p` | `top_p` | 透传 |
| `frequency_penalty` | `frequency_penalty` | 透传 |
| `presence_penalty` | `presence_penalty` | 透传 |
| `openai_max_tokens` | `max_tokens` | 透传（回复长度上限） |
| `openai_max_context` | `total_budget`（提示词总量预算） | 映射，用于预算裁剪口径 |
| `top_k` / `top_a` / `min_p` / `repetition_penalty` / `seed` / `n` | 同名保留 | **只保存与展示**（P4 实现）：这些参数当前**不透传**给 provider（国内托管 API 多会拒绝未知参数），界面需标注“当前模型忽略”；接口以 `extended_sampling` 单独返回，不静默假装生效 |
| `max_context_unlocked` | — | 忽略 |
| `chat_completion_source` / `openai_model` / `custom_url` / `reverse_proxy` / `proxy_password` 等 | — | **忽略且不落盘**（涉及用户密钥与端点，避免误存敏感信息） |
| `names_behavior` | 历史消息名前缀策略 | 实现：`0`=不加前缀、`1`=每条历史加「角色名: 」、`2`=仅群聊（本项目单人 → 等同 0） |
| `wrap_in_quotes` / `assistant_prefill` / `continue_prefill` / `continue_postfix` / `send_if_empty` / `impersonation_prompt` / `new_chat_prompt` / `new_group_chat_prompt` / `new_example_chat_prompt` / `continue_nudge_prompt` / `wi_format` / `scenario_format` / `personality_format` / `group_nudge_prompt` / `bias_preset_selected` / `stream_openai` / `squash_system_messages` / `use_sysprompt` / `media_inlining` / `show_external_models` / `bypass_status_check` | 见各条说明 | 逐项判定，见 §4.1 |

### 4.1 这些"非采样"字段的判定

| 字段 | 本项目 |
|---|---|
| `use_sysprompt` | **已实现**（P2）：为 true 时"System Prompt"覆盖生效（§6.4） |
| `show_thoughts` | **已实现**（P7）：映射为本项目 `enable_thinking`（同向："是否让模型思考"）；未提供时不覆盖内置档。因为是 `STPreset` 的 extra 字段（不在 `model_fields` 里），**不走 `sampling` 覆盖通道**，而是经覆盖层的 `assembly.show_thoughts` 改写，导出时写回原字段 |
| `squash_system_messages` | **已实现**（P2）：相邻无 name 的 system 消息合并 |
| `new_chat_prompt` / `new_example_chat_prompt` | **已实现**（P2）：新会话分隔、示例块分隔消息 |
| `wi_format` / `scenario_format` / `personality_format` | **已实现**（P2）：包装模板（`{0}` / `{{scenario}}` / `{{personality}}`）；模板缺占位符时**保留内容并告警**（ST 会静默丢内容） |
| `names_behavior` | **已实现**（P2）：用「角色名: 」内容前缀实现（不依赖 OpenAI 的 `name` 字段，兼容国内托管 API） |
| `send_if_empty` | **已实现**（P2）：空输入时的替代文本 |
| `continue_nudge_prompt` / `assistant_prefill` / `continue_prefill` / `continue_postfix` | **暂不实现**（本项目暂无续写生成入口）→ 导入时列入"未生效特性"提示 |
| `group_nudge_prompt` / `new_group_chat_prompt` | **忽略**（本项目无群聊），导入时列入"未生效特性"提示 |
| `wrap_in_quotes` | 忽略（ST 自身已标记弃用） |
| `bias_preset_selected` / `media_inlining` / `stream_openai` / `show_external_models` / `bypass_status_check` | 忽略（与本项目运行方式无关） |
| `max_context_unlocked` | 忽略 |
| 密钥/端点类字段 | 忽略且**不落盘** |

---

## 5. marker 占位映射表

marker 条目的正文为空，内容由运行时填充。映射如下（**左列为 ST 语义，右列为本项目内容来源**）：

| ST marker | 填充内容 | 来源 |
|---|---|---|
| `main` | 角色扮演总指令位（**预设自带正文**；用户未导入时为空，不内置 ST 原文） | 预设条目自身 |
| `worldInfoBefore` | 世界书命中块（插入角色定义之前） | `WorldBookEntry` 命中（现有 `matcher`） |
| `worldInfoAfter` | 世界书命中块（插入角色定义之后） | 同上 |
| `charDescription` | 角色定义主块 | HyPRA 人设 `persona.prompt` |
| `charPersonality` | 角色性格块 | 人设预设的 `title`/`tags`（可选增强，默认空） |
| `scenario` | 场景块 | 空（HyPRA 无场景字段；预留） |
| `personaDescription` | 用户（对话者）描述 | `user_name` 等已知信息（默认空） |
| `dialogueExamples` | few-shot 示例对话 | 文风预设 `style.examples` |
| `chatHistory` | 滚动窗口历史 | 热层 `history` |
| `jailbreak` | 尾部指令（Post-History Instructions） | 预设条目自身；本项目文风层可叠加（§6.6） |
| `nsfw` | 辅助提示（Auxiliary Prompt） | 预设条目自身（**不做内容审查替换**，由预设决定） |
| `enhanceDefinitions` | 补充设定指令 | 预设条目自身 |

> 未在表中出现的自定义 marker / 普通条目：按普通条目处理（正文来自预设）。

**Squash 例外**：`main`/`nsfw`/`jailbreak` 是 `system_prompt: true` 的正文条目，参与 §6.4 的覆盖逻辑。

---

## 6. 组装语义（本项目复现的算法）

单次请求的 messages 生成顺序（对齐 ST `public/scripts/openai.js` 的组装管道）：

```
① 收集扩展注入（extension prompts）
② 生成 Relative 条目消息（按 prompt_order 顺序；跳过 ABSOLUTE 条目）
③ 生成 marker 内容（§5 填充）
④ In-Chat 注入：按 depth 逐层插入（与扩展注入在该步合并）
⑤ 历史消息与本次用户输入
⑥ few-shot 示例（位置由 pin_examples 决定：默认插在历史之前）
⑦ 可选：合并相邻 system 消息（squash_system_messages）
```

### 6.1 Relative 条目（`injection_position = 0`）

按 `prompt_order[activeCharacter].order` 的数组顺序逐条产出消息；
`enabled = false` 的条目跳过；marker 条目在对应位置**就地展开**（§5）。
空 `content` 且非 marker 的条目跳过。

> `prompt_order` 有多份（按 `character_id` 区分）。本项目无角色卡概念 → 取 **`prompt_order[0]`**
> 作为生效顺序，并在导入时提示"该预设包含 N 份 per-character 顺序，已采用第 1 份"。

### 6.2 In-Chat 注入（`injection_position = 1`）

对齐 ST `populationInjectionPrompts`：

```
maxDepth = max(所有 In-Chat 条目的 injection_depth ∪ 所有扩展注入的 depth)
for depth in 0..maxDepth:
    该层条目 = 满足 injection_depth == depth 且 content 非空者
    按 injection_order 升序分组（缺省 100）
    每组内按 role 顺序（system → user → assistant）生成消息：
        content = 同组同 role 的多个条目正文用 "\n" 连接
                  + 该层该 role 的扩展注入文本（order == 100 时并入）
    该层产生的消息整体插入历史：
        depth 0 → 插在最后一条消息之后
        depth 1 → 插在最后一条消息之前
        depth n → 从末尾往前数 n 条的位置
```

要点：
- 同 depth + 同 order + 同 role 的条目会被**合并成一条消息**（这是 ST 的实际行为，必须复现，否则条目间距与模型表现不一致）。
- 扩展注入在同一层参与合并（这正是本项目记忆层的挂载点，见 §7）。

### 6.3 历史窗口与本次输入

- `chatHistory` marker 处展开滚动窗口（受历史预算裁剪，最旧优先丢弃）。
- 本次用户输入永远在最后（除非有 depth 0 的 In-Chat 注入，则注入在其后）。
- `names_behavior` 决定是否给历史消息加「角色名: 」前缀（§4.1）。

### 6.4 System Prompt 覆盖（`use_sysprompt`）与 `forbid_overrides`

- `use_sysprompt = true` 时，预设中 `system_prompt: true` 的条目的正文会被"System Prompt 覆盖文本"替换；
  `forbid_overrides = true` 的条目不参与替换。
- 覆盖文本来自**用户导入的预设/界面输入**，本项目不内置任何 ST 默认模板。
- 该开关默认 `false`（即不覆盖，原样使用预设条目正文），避免导入后正文被意外改写。

### 6.5 生成类型触发（`injection_trigger`）

- 空数组 = 所有生成类型都发送。
- 非空 = 白名单。本项目当前只有两种生成类型：`normal`（常规）与 `regenerate`（重新生成）。
- `continue` / `impersonate` / `swipe` / `quiet` 本项目未实现 → 导入了使用这些触发的条目时，
  在"未生效特性"清单中提示（不报错、不静默丢弃）。

### 6.6 HyPRA 特有层如何共存

本项目自有的**文风层**（`StylePreset.instruction_block`）与**记忆层**（§7）在 ST 预设中没有对应槽位：

- 记忆层 → 扩展注入（§7），参与 §6.2 的合并，用户可在界面调 depth/role/开关。
- 文风层 → 默认并入 `jailbreak`（尾部指令）位的**追加块**（可开关）。理由：该位置在 ST 生态中
  本就是"最后再叮嘱一句"的语义，与"表达风格约束"定位一致；关掉即纯预设行为。
  **P2 已实现**：`STRenderContext.style_text` 追加到 jailbreak 正文之后；
  预设未启用 jailbreak 槽位时追加到消息末尾并告警（不丢用户选的文风）。
- **不覆盖**预设作者的正文，只在其后追加，保证预设原意优先。

---

## 7. HyPRA 记忆层 → ST 扩展注入语义

对齐 ST 的 `setExtensionPrompt(key, value, position, depth, scan, role, filter)`：

| 项 | 取值 |
|---|---|
| key | `HYPRA_MEMORY`（单一键，内部已按 `[参考资料]/[记忆回忆]` 分节） |
| position | `IN_CHAT`（`IN_PROMPT` 与 `BEFORE_PROMPT` 作为可选模式） |
| depth（默认） | `1`（最后一条消息之前——紧邻本次输入，影响最强且不打断预设布局） |
| role（默认） | `system` |
| order（默认） | `100`（与 ST 扩展惯例一致：**同 depth · 同 order 的条目不区分来源**，按 system → user → assistant 合并成消息；因此若预设在同层有 user/assistant 条目，记忆块会排在它们之前） |
| 开关 | 界面可整体关闭；内部三块（个人记忆 / 情景记忆 / 语义事实）各有子开关 |
| 可调 | position / depth / role / order 全部可调，且改动写入本地覆盖层（§9） |

**兼容模式开关**（给"就想纯酒馆体验"的用户）：可把记忆注入位置切到
`worldInfoBefore`/`worldInfoAfter` 槽位（等价于方案②），或整体关闭（方案③）。

> 依据：ST `public/scripts/openai.js` 中 `getExtensionPrompt(IN_CHAT, depth, separator, role, wrap)`
> 在 In-Chat 注入阶段与预设条目按同层同 role 合并。

---

## 8. 不支持清单（导入时提示，不静默失败）

| 不支持 | 原因 | 提示方式 |
|---|---|---|
| STscript / Quick Reply / Regex 扩展的执行 | 属 ST 运行时能力，不在预设文件内 | 若预设含相关字段则列出 |
| Text Completion 预设的 Advanced Formatting | 与 Chat Completion 组装模型完全不同 | 只提取采样参数，界面提示"该预设为 Text Completion 格式，仅采样参数已导入" |
| group chat 字段（`group_nudge_prompt`、`new_group_chat_prompt`、群聊 prompt_order 策略） | 本项目为单人陪伴 | 提示"群聊相关设置未生效" |
| `continue` / `impersonate` / `swipe` / `quiet` 触发 | 本项目暂无这些生成入口 | 提示条目名与触发类型 |
| 续写/预填充设置（`continue_nudge_prompt`、`assistant_prefill` 等） | 无续写生成入口 | 提示"续写/预填充相关设置不生效" |
| 世界书递归 / 概率触发 / inclusion group | 属世界书（另立 C 档） | 世界书导入时提示 |
| 角色卡（Character Card）导入 | C 档 | 提示不支持 |

界面统一展示"**未生效特性清单**"，让用户一眼看到"这个预设哪些部分在我这里不生效"，而不是以为坏了。

---

## 9. 数据存储与覆盖层

```
backend/data/presets/                 # .gitignore 已覆盖（backend/data/）
  <preset-id>.json                    # 用户导入的原始文件（只读，永不改写）
  <preset-id>.override.json           # 本项目界面的编辑结果（开关/排序/depth/正文/注入设置）
  index.json                          # 清单：id、展示名、来源文件名、导入时间、原文件 hash
```

- **原始文件只读**：保证“重置为导入时”永远可用，也避免误改用户的 ST 预设。
- 运行时生效值 = 原始文件 ⊕ override（字段级覆盖）。
- **字段级恢复**：overlay 里某个键取 `null` 表示**删除该覆盖项**（回到预设原值）——
  否则一旦调过某项就再也回不到导入时的取值，只能整体重置。
- **非法枚举值写入前拒收**（400）：覆盖层是深合并的，存下非法值（如
  `names_behavior: 9`、未知 `role`）会把上一次的合法值顶掉，结果反而退回预设原值。
- 删除：删原始文件与 override；hash 变化（用户替换了同名文件）时提示并作废旧 override。

---

## 10. 接口契约（草案，P4 落地）

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/chat/st-presets` | 已导入预设清单（id/名称/来源/摘要/未生效特性） |
| `POST` | `/chat/st-presets/import` | 上传预设 JSON（multipart 或 JSON body），返回归一化结果 + 未生效特性 |
| `GET` | `/chat/st-presets/{id}` | 完整预设（归一化后 + override 合并 + marker 预览） |
| `PATCH` | `/chat/st-presets/{id}` | 写 override（`sampling` / `prompts` / `prompt_order` / `memory_injection` / `assembly`；值为 `null` = 删除该覆盖项） |
| `DELETE` | `/chat/st-presets/{id}` | 删除导入的预设 |
| `POST` | `/chat/st-presets/{id}/reset` | 清空 override |
| `GET` | `/chat/st-presets/{id}/export` | 导出为 ST 兼容 JSON（原样 + 当前 override 应用与否可选） |
| `POST` | `/chat` | 新增请求字段 `st_preset_id`（与内置 `preset_id` 相互独立；两者同传时以 `st_preset_id` 的组装结果为准，采样参数按 §4 合并） |

`st_preset_id` 与现有 `preset_id` 的分工：
- `preset_id`：内置模型档（采样参数 + style_hint），继续可用。
- `st_preset_id`：导入的 ST 预设（组装 + 采样）。同时指定时，组装走 ST 预设，采样参数按
  `内置档 → ST 预设 → 文风预设` 顺序后者覆盖前者。
- 两者都不传时走内置分层路径，行为与接入前完全一致（零回归）。

补充约定：
- `style_id` 传 `"none"` 表示**显式关闭文风层**。文风预设的采样参数优先级最高
  （会覆盖预设作者的意图），酒馆用户要“纯预设体验”时应关掉它。
- 预设文件存储在 `ST_PRESETS_DIR`（默认 `backend/data/presets`，`.gitignore` 已覆盖
  `backend/data/`）；预设不存在时 `POST /chat` 返回 404，**不静默退回内置路径**
  （静默退回会让用户以为预设生效了）。

---

## 11. 宏支持清单

ST 预设正文大量使用 `{{...}}` 宏。**P3 已实现**，落在**独立模块** `app/prompts/st_compat/macros.py`：

> 为什么不直接扩展 `state_vars/resolver.py`：两者名字空间与语义不同
> （ST 是 `{{char}}`，本项目是 `{{char_name}}`；ST 还有函数式 `{{getvar::}}`），
> 且独立模块能保证**内置分层路径零回归**——只有 ST 组装路径调用本模块。
> 宏变量按**会话隔离**（`MacroContext.variables` 由调用方按会话持有同一份字典）。

**已实现**

| 类别 | 宏 |
|---|---|
| 变量替换 | `{{char}}` `{{user}}` `{{description}}` `{{personality}}` `{{scenario}}` `{{persona}}` |
| 时间 | `{{time}}`（HH:MM）、`{{date}}`（YYYY-MM-DD） |
| 文本处理 | `{{//注释}}`（输出空）、`{{trim}}`（连同所在行换行一起删） |
| 会话变量 | `{{getvar::x}}` `{{setvar::x::v}}` `{{addvar::x::v}}` `{{incvar::x}}` `{{decvar::x}}` |
| 随机 | `{{random::a,b}}`（无参 = 0~1 浮点）、`{{pick::a,b}}`（同 seed 稳定）、`{{roll::1d20}}` |

**未实现**（未知宏**原样保留**，并计入 `RenderedPrompt.unresolved_macros` 供界面提示，不静默吞掉）：
`{{weekday}}` `{{isotime}}` `{{isodate}}` `{{bias}}` `{{outlet::}}`、STscript 变量/条件宏等。

**作用范围**（明确边界）：

| 文本 | 是否过 ST 宏 | 理由 |
|---|---|---|
| 预设条目正文、marker 填充、格式模板 | ✅ | 预设产生的文本 |
| 示例对话、`new_chat_prompt`、`send_if_empty` | ✅ | 同属预设内容 |
| 历史消息、本次输入 | ❌ | 已发生的对话原文，改写会与界面展示不一致 |
| HyPRA 记忆块、文风块 | ❌ | 各自有 loader/渲染器负责（含本项目自己的 `{{变量}}` 宏） |

---

## 12. 与现有代码的边界（不回归）

| 现有模块 | 是否改动 | 说明 |
|---|---|---|
| `app/rag/prompt_manager.py` | **不动** | 内置分层路径完全保留；两条路径在 `nodes.assemble_prompt` 分派 |
| `app/prompts/persona`、`style` | 只读复用 | 作为 marker 内容来源 |
| `app/worldbook` | 只读复用 | `worldInfoBefore/After` 的内容来源（择一注入，见 §6） |
| `app/llm/profiles.py` | 不动 | 三级采样合并在 `nodes._resolve_sampling_with_st` 里显式实现 |
| `app/prompts/state_vars/resolver.py` | **不动** | ST 宏落在独立模块 `st_compat/macros.py`，内置路径零回归（见 §11） |
| `app/api/chat.py` | 已改（P4） | 7 个预设端点 + `st_preset_id` 接线 + 宏变量回写（`mv:` 前缀存 state_vars） |
| `app/graph/nodes.py`、`graph/state.py` | 已改（P4） | 组装分派、记忆拼装、世界书槽位选择、三级采样合并 |
| `app/config.py` | 已改（P4） | 新增 `st_presets_dir` |
| 前端 `PresetSwitcher` 等 | 待改（P5/P6） | 见 §13 |

---

## 13. 前端落点（P5/P6，**已实现**）

| 组件 / 模块 | 职责 |
|---|---|
| `components/studio/StPresetPanel.tsx` | 折叠面板：导入（文件选择 + 拖放）、清单与选择、导出、恢复导入时、删除（内联二次确认）、上一轮生效摘要 |
| `components/studio/StPresetEditor.tsx` | 编辑器：采样参数、组装开关、条目清单（开关 / 拖拽 / ↑↓ / 展开编辑）、记忆注入、未生效特性与兼容提示 |
| `lib/studio/st-preset.ts` | 纯逻辑：字段元信息、顺序表与条目合并、排序、补丁深合并、token 估算 |
| `lib/api/client.ts` / `lib/api/types.ts` | 7 个端点封装 + 与后端对齐的类型 |
| `hooks/chat/useChatSession.ts` | `stPresetId` 状态 + `POST /chat` 传 `st_preset_id` + 收集 `st_preset` 元信息 |

交互要点：
- **乐观更新 + 防抖提交**（500ms）：改本地 state 立即反馈，补丁经 `mergePatch` 累积后一次提交；
  组件卸载前排空未提交改动（用户调完就切走不会静默丢改动）。
- **排序双通道**：HTML5 拖拽 + 键盘可达的 ↑↓ 按钮（拖拽在触屏/读屏下不可用）。
- **占位条目不提供文本框**，改为显示“正文由运行时填充（内容来源）”；
  “不可编辑”是后端的硬约束，界面不能只是看着能改。
- **扩展采样参数可见但标注忽略**，并提供“恢复预设原值”按钮（提交 `null`）。
- **文风层开关**：后端支持 `style_id="none"`；前端当前由请求参数控制（面板未单独加开关）。

未实现项（诚实标注）：**预设重命名**（展示名取导入文件名，无重命名端点）。

> 合规：界面自写（现有 Tailwind 设计语言），只借鉴功能结构与交互范式；不抄 ST 的 DOM/CSS/文案。

---

## 14. 分阶段落地

| 阶段 | 交付 | 验收 | 状态 |
|---|---|---|---|
| P0 | 本文档 | 评审确认 | ✅ 已确认 |
| P1 | `app/prompts/st_compat/{models,parser,store}.py` | 解析器单测（自造夹具：完整预设 / 缺 `prompt_order` / 旧字段 / 未知字段保留 / BOM） | ✅ 已实现（45 项测试） |
| P2 | `{markers,renderer}.py` | 组装语义单测：Relative 顺序、In-Chat depth 插入、同层同 role 合并、marker 填充、squash | ✅ 已实现（34 项测试） |
| P3 | 宏解析扩展 | 宏单测 + 未知宏保留 | ✅ 已实现（独立模块 `macros.py`，24 项测试） |
| P4 | API + graph 分派 + 记忆扩展注入 | 接口测试 + 组装端到端测试（mock LLM） | ✅ 已实现（33 项测试） |
| P5 | 前端预设面板 | 前端测试 + lint/build | ✅ 已实现（`StPresetPanel`） |
| P6 | 前端条目编辑器 | 同上 | ✅ 已实现（`StPresetEditor` + `lib/studio/st-preset.ts`） |
| P7 | README / 文档同步 / 合规声明段落 | 全量 pytest + 前端 build | ⬜ 待做 |

---

## 附：字段来源索引

| 事实 | 来源 |
|---|---|
| `INJECTION_POSITION = { RELATIVE: 0, ABSOLUTE: 1 }`、`DEFAULT_DEPTH=4`、`DEFAULT_ORDER=100`、`Prompt` 字段表 | `public/scripts/PromptManager.js` |
| 默认 marker 清单与 `prompt_order` 结构 | `default/content/presets/openai/Default.json` |
| Relative/In-Chat/Depth/Order/Triggers 语义 | ST 官方文档 `Usage/Prompts/prompt-manager.md` |
| `populationInjectionPrompts` 的 depth 循环、order 分组、按 role 合并、扩展注入并入 | `public/scripts/openai.js` |
| `extension_prompt_types`（BEFORE_PROMPT / IN_PROMPT / IN_CHAT）与 `extension_prompt_roles` | `public/scripts/extensions.js`、`public/scripts/openai.js` |
| `squashSystemMessages` | `public/scripts/openai.js` |
| `populateDialogueExamples` | `public/scripts/openai.js` |
