# HyPRA 项目约定（AGENTS.md）

> 约束本项目内的开发与协作。个人通用习惯见全局 `~/.pi/agent/AGENTS.md`；
> 两处冲突时，以本项目文件为准（项目级优先于全局级）。

## 1. 项目定位

HyPRA：打通「提示词架构」与「混合记忆」的情感陪伴 3D 交互系统（Web 应用，参赛项目）。
目标用户：高压快节奏生活中的现代人，提供符合幻想的 AI 情感陪伴，化解日常压力。
本项目借鉴 SillyTavern 的提示词工程与记忆体系**思想**及其数据格式规范，
除明确标注来源的第三方组件外代码为原创实现；项目以 **GNU AGPL-3.0** 发布（见 §6）。
提供两种交互模式：**桌宠对话模式**（跨会话长期陪伴，现状）与**酒馆聊天模式**
（还原酒馆原生体验）——模式定义、记忆作用域与实施顺序见 §8。

## 2. 技术栈（版本随安装锁定后更新本表）

- 后端：Python + FastAPI + LangChain / LangGraph
- 记忆：**Qdrant 向量库（双模式：本地 Docker 优先，云可切）**（温层向量库，经 `WarmMemoryStore` 接口抽象接入；开发可用云，评审用 docker compose 本地部署）+ SQLite/JSON（冷层结构化）+ 内存滚动窗口（热层）
- 模型：Qwen2.5-7B 等开源模型，走国内托管 API（阿里百炼 / 硅基流动）
- 多模态：魔珐星云具身驱动 SDK（数字人实时渲染）+ SSML 播报指令（KA 动作）→ 前端 `speak()` 驱动
- 前端：React / Next.js（对话 UI + 数字人视频播放），App Router
- 情绪输出：function calling 结构化输出为主，正则仅作兜底

## 3. 目录结构约定（新建代码须放入对应分层）

    backend/            # Python 后端
      app/
        plugins/        # 插件体系宿主（框架层，见 §9）：registry / manifest / context / manager
                        #   —— 与业务实现解耦，不反向依赖 app 内其它模块
        api/            # FastAPI 路由（chat / studio / knowledge / llm / media / avatar_models / st_presets）
        graph/          # LangGraph 编排：对话图 / 节点 / 状态
        llm/            # 模型接入：provider 工厂 + OpenAI 兼容实现 + profiles.yaml（模型预设）+ 推理参数
        memory/
          warm/         # 情景记忆：WarmMemoryStore 接口 + Qdrant/内存实现
          cold/         # 语义记忆：结构化事实表（三元组 + importance + 生命周期状态）
          knowledge/    # 个人记忆：用户上传语料（清洗/去重/解析/分块/混合检索）
          store.py      # 记忆门面：聚合各层，产出「世界书之后、滚动窗口之前」的记忆块
                        #   注：**工作记忆（滚动窗口）不在本目录**，由 app/session/ 承载
        session/        # 会话与工作记忆：滚动窗口上下文 + 会话持久化（SQLite）
        mcp/            # MCP **Client**：连接外部 MCP server，把工具接入 Agent 行动层
        tools/          # function calling 工具（情绪/记忆写入/内置工具注册表/技能学习）
        skills/         # Skill 体系：渐进式加载的能力说明（§9.6，独立于插件）
        worldbook/      # 世界书条目：YAML + 关键词/正则/向量三重触发匹配
        studio/         # 用户创作：自建角色卡 / 背景故事 / 世界书条目的存储与编辑（内置只读，
                        #   数据落 backend/data/studio，设计见 docs/user-content-studio.md）
        prompts/        # 分层 System Prompt 模板（预设内容与代码分离）
          persona/      #   人设预设（借鉴 SillyTavern 预设：预设与代码分离）
          style/        #   文风预设（A/B 对比演示用）
          state_vars/   #   动态状态变量定义（如 {{current_mood}}）
          st_compat/    #   酒馆预设兼容：解析 / 宏 / 渲染 / 存储
          adaptation/   #   预设 AI 适配：检测 / 差异 / 规划（rules.yaml）
        rag/
          retrieval/    # 混合检索底座：分词 / BM25 / RRF 融合（情景记忆与知识库共用）
          prompt_manager.py  # 分层注入与 token 预算（PromptManager）
        tts/            # 语音：音色表 / 音频处理 / GPT-SoVITS 接入
        digital_human/  # 具身驱动：魔珐星云 SDK 对接 + SSML + viseme + 模型库
                        #   model_sources/ 模型来源（本机库 / 清单），见 §9.10 第 15 项
        config.py       # 全部可配置项（.env 读入，含本地/云双模式开关）
        main.py         # FastAPI 应用工厂 + 路由注册 + 生命周期（MCP 连接/断开）
      mcp_servers/      # 本项目**自带**的 MCP Server（独立进程，不属于 app 包，见其 README.md）
                        #   ⚠️ 与 app/mcp（Client）区分：这里是被连接的「外部服务」侧
      skills/           # 内置技能包（每技能一个 <id>/SKILL.md，入库随项目分发）
      plugins/          # 第一方插件（目录 + manifest.json 形态，见 §9.3）
        tavern-bridge/  #   酒馆数据只读接入（首个 datasource 插件）
        live2d-model-source/  #   数字人模型来源（第二个 datasource；只读，不代下载）
      scripts/          # 一次性运维/实验脚本（预设导入 / A-B / 探针），不参与运行时
      tests/            # pytest：目录与 app/ 分层一一对应（test_app / test_llm / test_memory / …）
      data/             # 运行时数据（SQLite / 预设覆盖层 / logs/）—— gitignore
      media/            # 生成的语音与视频产物 —— gitignore
    frontend/           # Next.js 应用（App Router）
      app/              #   页面与布局
      components/       #   按域分组：chat / avatar / studio / settings
      hooks/            #   按域分组：avatar（具身与语音）/ chat（对话编排）
      lib/              #   按域分组：api（接口封装 client.ts + 契约 types.ts）/ avatar（渲染与口型）
                        #     / chat（会话与本地偏好）/ studio / live2d（三态桥，路径不可动）
      vendor/cubism/    #   Cubism SDK（脚本落位，不入库）
      tests/            #   vitest
    desktop/            # Electron 桌宠端（透明桌宠窗 + 托盘）
      src/main/         #   主进程：窗口/穿透/托盘/设置/loopback 静态资源服务
      src/preload/      #   contextBridge 白名单（sandbox 下只能 require electron）
      src/renderer/pet/ #   桌宠窗（Vite 独立入口；经 `@/` 复用 frontend 的渲染器与 hooks）
      src/shared/       #   两端共享的 IPC 契约（禁 electron / react 依赖）
    docs/               # 参赛说明文档、演示脚本、设计文档
    _local/             # 本机私有资料：自备 SDK/模型包、开发留档、旧备份、生成缓存 —— gitignore
                        #   目的：仓库根目录只出现项目本身
    README.md  LICENSE  .gitignore  docker-compose.yml

> **改动目录结构时的硬约束**：`frontend/lib/live2d/` 是 Cubism 三态桥
> （接口 `cubism-bridge.ts` / 生成实现 `vendor/cubism/bridge.ts` / 降级实现 `cubism-bridge.unavailable.ts`），
> 其路径被 `frontend/tsconfig.json`、`frontend/next.config.mjs`、`frontend/vitest.config.mts`、
> `desktop/vite.config.mts` 四处 alias 直接引用——**移动该目录会直接破坏构建**，
> 改动前须同步这四处并重跑两端构建。其余前端目录移动后需同步更新
> `@/lib|@/components|@/hooks` 引用与 docs 中引用的路径。

## 4. 关键设计约束

- 提示词工程：System Prompt 分层（全局人设 / 状态变量 / 对话格式），预设内容与代码分离；
  世界书条目用 JSON/YAML，触发机制为关键词 + 正则 + 向量三重触发，注入位置分档并可配 token 预算。
- 记忆体系：**按认知心理学分四层**——工作记忆（滚动窗口）/ 情景记忆（对话片段的混合检索
  召回）/ 语义记忆（结构化事实常驻）/ 个人记忆（用户上传的私人语料）。
  设计见 `docs/memory-architecture.md`。借鉴 SillyTavern 及其插件生态（Data Bank /
  Chat-vectorization 与社区记忆插件）的写入-召回机制，**具体实现按
  `docs/sillytavern-memory-design-reference.md` 的「8 条可落地参照」执行**，
  要点：回复后事件驱动抽取、混合检索（BM25 + 稠密向量 → RRF 融合）、激活词/情绪加权、
  时间衰减、每「陪伴对象」独立 Qdrant collection 实现记忆隔离
  （记忆 scope 已泛化为 `"{scope_type}:{scope_key}"` 字符串，见 §8.2）。
  Summarize 机制**分模式处理**（见 §8.3）：`companion` 模式不采用——职责与工作记忆
  窗口重叠，改由情景记忆与语义记忆分担（理由见架构文档附录 P7）；`tavern` 模式**必须采用**。
- **function calling + MCP 双主线（核心技术）**：LLM 回复强制走 function calling 结构化输出
  8 类情绪标签（正则仅作兜底），情绪标签同时驱动 3D 表情映射与记忆召回加权；并通过 MCP 协议
  把外部 MCP server 的工具桥接进 Agent 行动层（命名 `mcp__<服务器>__<工具>`），
  模型可自主调用外部能力「办事」（连接失败不影响启动，协议与传输由官方 SDK 负责）；
  文本 + 情绪标签 → SSML（含 KA 动作）→ 魔珐星云具身驱动 SDK 实时渲染，
  并实现渲染无关的驱动时间轴，可降级接入任意 3D/2D 模型。
- RAG 拼接优先级（PromptManager 固定顺序）：世界书触发 > 个人记忆 > 情景记忆 > 语义事实 > 滚动窗口。
- 桌面桌宠端（`desktop/`）：与 Web 端**共用后端与渲染层**，不重写对话/记忆；
  桌宠窗是 Vite 独立入口（不依赖 Next 服务），页面由主进程的 loopback 静态资源服务提供
  （前端资源引用是绝对路径 `/live2d`、`/portraits`，`file://` 下会 404），端口固定优先以
  保证 localStorage origin 稳定。主进程**不接触任何模型 API Key**，不代理业务请求。
  发布包不含模型/音频（白名单拷贝 + `scripts/verify-release-assets.mjs` 硬校验），
  用户自备模型放 `%APPDATA%/HyPRA/live2d/`。设计见 `docs/desktop-pet.md`。

## 5. 开发与验证

- 完成任一模块后必须运行验证：后端 `backend/tests`（pytest）；前端按 frontend 现有脚本（lint / build）；
  桌面端 `desktop` 下 `npm run typecheck` + `npm test` + `npm run build`（build 末尾会做发布物资源边界校验）。
- 新增大模型 / 新增第三方依赖前，先列出方案征求确认。
- 云端 key（百炼 / 硅基流动 / 魔珐星云 / Qdrant）一律放 `backend/.env`，不入库。
  双模式：评审交付用 docker compose 一键部署（本地 Qdrant），embedding/LLM 由评审在 .env 自填。

## 6. 许可与合规红线（参赛硬指标）

- **项目协议：GNU AGPL-3.0**（`LICENSE` 全文，Copyright 2026 nobody5323；
  各包声明统一为 SPDX `AGPL-3.0-only`）。因此：不得闭源分发；
  **以网络服务形式提供时必须履行 §13**——让用户能获取对应版本的源码。
  实现方式与检查清单见 `docs/license-compliance.md`。
- **代码来源纪律**：借鉴 SillyTavern / TauriTavern / tellev / 社区插件（均为 AGPL-3.0）时
  **可以先复用代码**，但必须①在文件头标注来源与提交 hash、②保留原版权声明、
  ③登记到 `docs/license-compliance.md` 的「第三方借鉴清单」。未登记的复用视为违规。
  **不得再声称「不受 AGPL 传染」**（本项目自身即 AGPL），也不要再写「100% 原创」这类
  绝对表述，改用「除明确标注来源的第三方组件外为原创实现」。
- **数据格式 ≠ 代码**：角色卡（CCv2/CCv3）、世界书、预设等属社区公开规范，
  实现兼容不受限；但引用他人**内容**（角色卡正文、提示词原文、语料）仍受其版权约束。
- **插件许可边界**：进程内耦合（同进程 import / 同一 JS 上下文）的插件视为衍生作品，
  须以 AGPL 兼容协议授权；通过**进程外或隔离边界**（子进程 / MCP / iframe / Worker）
  交互的插件视为独立程序，作者可自选协议。宿主 UI 必须明示该边界，不得含糊。
- 测试语料 / 世界书 / RAG 语料只使用自创或公有领域内容；
  借鉴社区插件只取机制思想，不复制提示词原文。
- **勘察与导入数据**：本地酒馆实例的角色卡 / 世界书内容一律不入库、不作演示素材（见 §8.6）；
  导入用户自有数据属用户行为，但导入内容不得随产品分发。
- 提交前检查：.env、node_modules、.venv、向量数据、生成的视频/语音等一律 gitignore，
  不提交无关文件。新依赖入库前先看其协议（见 `docs/license-compliance.md` 的依赖审计）。

## 7. 参赛文档产出

- docs/项目说明文档（按既定 PDF 大纲撰写）
- docs/演示脚本（3–5 分钟：长对话记忆召回、情绪变化→3D 表情联动、世界书触发）
- docs/sillytavern-memory-design-reference.md（记忆机制参照，已建立）
- docs/desktop-pet.md（桌面桌宠端：形态分工、进程结构、关键决策、合规边界，已建立）
- docs/license-compliance.md（许可与合规说明：AGPL 义务、§13 检查清单、来源纪律、插件许可边界、依赖审计，已建立）
- README.md / LICENSE

## 8. 双模式与记忆作用域（已定架构决策）

> 依据：本地酒馆实机勘察（ST 1.18.0）+ 本项目现有代码核实。
> **勘察数据的内容一律不得进仓库**（见 §8.6）。

### 8.1 两种交互模式

| 模式 | 定位 | 提示词来源 | 具身 / 桌宠层 |
| --- | --- | --- | --- |
| `companion`（桌宠对话，现状） | 跨会话长期陪伴 | HyPRA 分层提示词 + 世界书 + 记忆四层 | 启用（数字人 / 桌宠窗 / 情绪 / 语音） |
| `tavern`（酒馆聊天） | 还原酒馆原生体验 | **仅**酒馆预设 + 角色卡 | 关闭 |

- 模式是**会话级属性**（落在 session 上）：切换模式 = 新建会话。不做逐轮参数，
  避免同一会话的上下文语义漂移。
- 编排层用**两条独立组装策略**分派，禁止 if/else 渗透进 `graph/nodes.py`。

### 8.2 记忆作用域（本次决策核心）

记忆 scope 键由 `companion_id` 泛化为字符串 `"{scope_type}:{scope_key}"`
（用字符串而非元组：存储层签名只改语义、不改结构，最小侵入；`:` 等非法字符由存储层 slug 化处理）。

| 模式 | scope 键 | 记忆机制 | 存储 | 会话压缩 |
| --- | --- | --- | --- | --- |
| `companion` | `companion:{persona_id}` | 温层向量召回 + 冷层事实 | `memory_companion_{id}` collection / `facts_companion_{id}` 表 | 不用 Summarize（靠情景 + 语义记忆） |
| `tavern` | `session:{session_id}` | **仅会话摘要**；会话内向量召回为**可选开关**（默认关） | 摘要存**会话记录**，不逐会话建 collection | **用 Summarize**（会话级滚动摘要） |

- `tavern` 模式的向量召回默认**关闭**：酒馆实机形态本就不含向量层（实测用户 `vectors: 0`），
  默认开启只会徒增「每会话一个 collection」的实现与运维成本；将来按开关启用，不影响架构。
- **存量数据迁移：写迁移脚本**（重命名 collection 与表，保留已有记忆）——
  不采用「读时双命名兼容」（留技术债）或「丢弃重来」（不该在重构里丢用户的记忆）。
- **两模式记忆默认完全隔离**（互不读写），避免桌宠积累的记忆污染酒馆的纯净预设体验。
- 隔离单位从「陪伴对象」升级为「作用域」，`WarmMemoryStore` / `ColdMemoryStore` 的键随之泛化。
- 个人记忆（`knowledge`，用户上传语料）**数据两模式共享**，但**注入与否由模式决定**（`tavern` 不注入）。
- 现状说明：记忆本来就是按作用域跨会话累积的（温层 collection / 冷层表都不含 `session`），
  只有滚动窗口是会话内的——`companion` 模式的「跨会话长期记忆」已成立，无需新建。

### 8.3 Summarize：分模式结论（修订 §4）

原结论「未采用 Summarize」**仅对 `companion` 模式成立**；`tavern` 模式**必须采用**
（纯预设模式下没有记忆层，长会话只能靠会话级摘要压缩）。实机勘察到的实现要点：
摘要**存在会话（chat）上**、注入位置 `IN_PROMPT` / `depth=2` / `role=system`、
模板 `[Summary: {{summary}}]`、按消息条数间隔触发更新。

### 8.4 酒馆角色卡接入范围

> **档位与插件体系的对应关系见 §9.8**：A 档并入 `tavern-bridge` 插件；B 档作为插件 hook 能力实现；C 档搁置。

分档实施，**A / B / C 三档全部纳入范围**（分期交付）：

| 档 | 内容 | 次序 |
| --- | --- | --- |
| **A 设定层** | `description` / `first_mes` / `alternate_greetings` / `mes_example` / `character_book` / `depth_prompt` | 先做 |
| **B 正则脚本层** | `extensions.regex_scripts` 执行（findRegex → replaceString） | 次做 |
| **C JS 脚本层** | `extensions.tavern_helper` 的 JS 脚本**沙箱** | 后做，须有明确执行边界与文档声明 |

实机勘察得到的**三条硬性实现约束**（不遵守会读到空设定）：

1. 角色卡是 PNG `tEXt` chunk，键为 `chara`(V2) 与 `ccv3`(V3)，**两个值都是 base64 编码的 JSON**；
   解析时 `ccv3` 优先、`chara` 回退。
2. **V1 字段回填是必需的**：实测 `data.description` 为空而正文在**顶层** `description`，
   必须做 V1 ↔ V3 双向合并。
3. 真实卡的设定正文几乎全在 `description` + `character_book`；
   `personality` / `scenario` / `system_prompt` / `post_history_instructions` 实测**基本为空**，
   映射优先级靠后。

`character_book` 直接映射为 `WorldBookEntry`（`scope` = 该角色 id，现有字段已支持）；
但酒馆条目字段远多于本项目现有模型，需补齐：`position` + `depth`、`constant`、
`selective` + `selectiveLogic`、`probability` / `useProbability`、`sticky` / `cooldown` / `delay`、
`group` 系列、`recursion` 三件套、`role`、跨字段匹配（`matchPersonalityDescription` 等）、
`triggers`、`scanDepth` / `matchWholeWords`。

### 8.5 实施顺序（已确认）

1. **记忆作用域泛化 + `tavern` 模式会话级摘要**（双模式的地基，优先做）
2. 角色卡接入 **A 档** → 世界书字段补齐
3. 角色卡 **B 档**（正则脚本）→ **C 档**（JS 沙箱，分期）
4. 双模式切换（前端 UI + 编排分派）
5. Live2D 模型自动兼容（独立，可并行）
6. 插件体系与插件市场（方向已定，参照实现待定）

插件体系的**合规与安全边界**（先立规矩）：

- 市场只做**索引与安装机制**，不托管第三方代码；参赛提交物内**不含任何第三方插件**，示例插件全部自研。
- 用户可写插件 = 任意代码执行：文档必须写明「本地可信」边界，**不得暗示存在沙箱**
  （C 档 JS 脚本若实现沙箱，须把能力边界写清并单独说明）。

### 8.6 勘察数据的使用边界（红线，与 §6 同级的硬约束）

本地酒馆实例仅作为**格式与机制的样本来源**：

- 其角色卡 / 世界书的**内容**（含成年人内容且非自有版权）**一律不得**进入仓库、
  测试夹具、演示素材或截图；
- 测试与演示所需角色卡一律**自创**，且须覆盖边界情况（V1 字段为空、双 chunk、内嵌世界书、正则脚本）；
- 对本地酒馆目录**只读**：不写入、不把内容复制进项目。

## 9. 插件体系（全部插件化）

> 依据：Alife（`BDFFZI/Alife`，C#）的 `ModuleAttribute` / DI / Skill / 市场机制借鉴，
> 以及本项目既有扩展点盘点（§9.1）。核心原则：**策略是核心，实现是插件**。

### 9.1 现状：项目已有 7 个「事实上的插件点」

它们早就是 `base.py(ABC 接口) + factory.py(按名创建) + config 字符串开关` 的模式。
**插件体系不发明新机制**，只把它显式化、统一化（现在一个 config 字符串，
插件化后是「可管理 / 可展示 / 可第三方扩展」的能力）：

| 能力 | 接口 | 工厂 | 已有实现 | 原 config 开关 |
| --- | --- | --- | --- | --- |
| LLM provider | `llm/base.py: LLMProvider` | ✅ | mock / openai_compatible | `llm_provider` |
| 数字人驱动 | `digital_human/base.py: DigitalHumanProvider` | ✅ | local / xmov / gpt_sovits | `digital_human_provider` |
| 情景记忆存储 | `memory/warm/base.py: WarmMemoryStore` | ✅ | inmemory / qdrant | `warm_backend` |
| 知识库存储 | `memory/knowledge/base.py: KnowledgeStore` | ✅ | inmemory / qdrant | `knowledge_backend` |
| 会话持久化 | `session/base.py: SessionStore` | ✅ | sqlite | `session_backend` |
| function calling 工具 | `tools/registry.py: ToolSpec` | ✅ | 4 个内置 | — |
| MCP server | `mcp/manager.py` | — | wellness | `mcp_servers.json` |

### 9.2 「全部插件化」的三层

一切能力都经**同一个注册表**暴露；按必需性分三层：

| 层 | 含义 | 可禁用 | 内容 |
| --- | --- | --- | --- |
| `core` | 换掉它产品就不成立 | ❌ 不可 | 提示词组装与预算、记忆门面与衰减策略、编排图、滚动窗口、世界书三重触发、情绪链路、驱动时间轴、RRF 融合 |
| `builtin` | 随项目分发、默认启用，可替换/禁用 | ✅ | LLM/数字人/记忆后端/工具/解析器/分词器/MCP/ST 适配/酒馆接入 |
| `third-party` | 用户安装 | ✅ | 社区插件 |

### 9.3 统一契约（对标 Alife `ModuleAttribute`）

| Alife 字段 | HyPRA 对应 | 说明 |
| --- | --- | --- |
| `name` / `description` / `url` | `display_name` / `description` / `homepage` | 展示与来源 |
| `launchOrder` | `loading_order` | 加载顺序（沿用 ST 习惯） |
| `defaultCategory` | `category` | 管理 UI 分组 |
| **`editorUI`**（Type） | **`settings_schema`**（JSON Schema） | 宿主自动渲染设置表单 |
| `globalUI`（Type） | `ui_slots`（**v1 不做**） | 全局界面挂载点 |
| DI 构造注入 | `PluginContext` | 宿主注入的能力门面 |
| `Awake/Start/Destroy` | `setup(ctx)` / `start()` / `shutdown()` | 生命周期，挂到 `main.py` lifespan |

manifest 示例（`plugins/tavern_bridge/manifest.json`）：

```json
{
  "id": "tavern-bridge",
  "display_name": "酒馆数据接入",
  "version": "1.0.0",
  "layer": "builtin",
  "category": "datasource",
  "loading_order": 50,
  "capabilities": ["datasource", "prompt", "tool", "hook", "settings"],
  "permissions": { "filesystem": { "read": ["${tavern_data_dir}"], "write": false }, "network": [] },
  "settings_schema": {
    "type": "object",
    "properties": { "data_dir": { "type": "string", "title": "酒馆数据目录" } },
    "required": ["data_dir"]
  },
  "entry": "plugin.py"
}
```

**权限是架构约束，不是口头承诺**：宿主在把能力交给插件前**强制校验** `permissions`，
`write: false` 的插件拿不到写句柄（§8 的「绝不修改酒馆数据」由此保证）。

白名单项支持两类占位符：插件自己的配置键（`${tavern_dir}`）与**宿主内置变量**
`${data_dir}`（宿主数据目录）。内置变量的存在理由：插件常需要读宿主放在 `data` 下的
公共资源（如本机模型库），而这个位置是**宿主决定的**——让 manifest 写死相对路径、
或让用户重填一遍绝对路径，都不合理。未解析的项（用户没配）连同该项一起跳过：
拿不到就不给读。

### 9.4 能力面（capability）

| capability | 作用 | 首个使用者 |
| --- | --- | --- |
| `provider` | 提供某类能力的实现（**按接口注册**，供工厂选中） | LLM / 数字人 / 记忆后端 |
| `tool` | 注册 function calling 工具 | `tools-builtin` / MCP / 酒馆接入 |
| `datasource` | 读取外部数据 → 输出**中性结构化对象**（映射到宿主模型由宿主做） | `tavern-bridge` / `live2d-model-source` |
| `prompt` | 往注入点插提示词片段 | `tavern-bridge` / `st-format-adapter` |
| `hook` | 订阅对话生命周期事件 | 记忆构建 / regex_scripts 执行 |
| `settings` | 声明式配置（JSON Schema） | 全部 |

**`datasource` 的分工是硬约定**：插件只负责「读 + 产出中性数据」，
→ `WorldBookEntry` / persona 的映射**由宿主完成**，保证多数据源共享一套模型。

### 9.5 配置的声明式 UI

Alife 用 C# 特性（`[DisplayName]`/`[Description]`）反射生成表单；HyPRA 用 **JSON Schema → 宿主渲染**。

理由：插件**不写 React 组件**，避开运行时加载前端代码的全部代价（React 单例、版本契约、CSS 隔离）。
这同时是 §9.7 「不做 plugin UI」的替代方案，不是权宜之计。

### 9.6 Skill 体系（借鉴 Alife `SkillService`）—— ✅ 已实现

- 位置：`backend/skills/<id>/SKILL.md`（内置）+ `backend/data/skills/`（用户添加）
- `SKILL.md` = YAML frontmatter（`name` / `description` / `when_to_use`）+ Markdown 正文
- **渐进式注入**：常驻只注 `name: description` 清单（几十 token）；模型调 `study_skill(name)` 才把正文吐回上下文
- 禁用：`SKILLS_DISABLED` + 界面启停（**落盘** `data/skills.json`，与插件同理由）
- 与 pi 的 skills 同范式，格式可对齐

落地要点（`app/skills/` + `app/tools/skills.py` + `app/api/skills.py`）：

| 环节 | 位置 |
| --- | --- |
| 解析与扫描 | `skills/loader.py`（容错 BOM / 缺 frontmatter / YAML 写坏；目录形与单文件形都收） |
| 装载与启停 | `skills/registry.py`（内置优先于用户同名；全局单例，`create_app` 装载） |
| 常驻清单 | `registry.catalog_text()` → `PromptManager` 的 skills 层（priority 75） |
| 按需正文 | `tools/skills.py: study_skill`（**当轮生效**，不常驻上下文）；空技能库时不注册 |
| 管理接口 | `GET /skills`、`GET /skills/{id}`、`POST /skills/{id}/enabled`、`POST /skills/reload` |
| 前端 | `components/settings/SkillPanel.tsx`（清单来自 `/health`，正文展开时才拉） |

`/skills/reload` 与 §9.7「插件不做热重载」不矛盾：技能是**文件形态**的，
重扫只重读几个 Markdown；插件重载要重跑插件代码。

### 9.7 v1 明确不做

| 项 | 理由 / 替代 |
| --- | --- |
| 插件自带 React UI | 用 §9.5 的 JSON Schema 替代 |
| JS 沙箱 / ST 扩展运行时 | 已评估否决（见 §9.8），C 档搁置 |
| 自动热重载（Alife 有） | 开发期用 `uvicorn --reload` 足够 |
| 依赖自动安装（Alife 有 Pip/NuGet installer） | 插件声明的依赖由用户手动装；市场阶段再议 |
| 插件签名 / 校验 | 本地可信边界先立住（§6） |

### 9.8 与酒馆兼容的关系（修订 §8.4）

- 插件体系是**宿主**，`tavern-bridge` 是它的第一个 `datasource` 插件。
- 原 §8.4 的 A/B/C 档重定位：**A 档（设定层）并入 `tavern-bridge`**；
  **B 档（regex_scripts）**作为插件的 `hook` 能力实现；**C 档（JS 沙箱）搁置**。
- **已评估否决的路线**（证据存 `docs/`）：复用 ST 插件系统（依赖闭包 164 文件 / 12.2 万行 = ST 前端 84%）、
  iframe 虚拟 DOM 沙箱、双前端双后端。
- 与酒馆的关系定为：**只读接入数据 + 数据格式兼容**，不做 ST 客户端。

### 9.9 插件市场（远期）

借鉴 Alife：`Plugin` / `PluginRelease` / `VersionResolver` / `FileSystemPluginManager`
+ 安装计划与回滚。合规边界见 §6：只做索引与安装机制、**不托管第三方代码**、
参赛提交物内不含任何第三方插件。

### 9.10 全部插件化清单

**core（9 项，不可禁用）**：`prompt-assembly`（assemble/renderer/sanitize + prompt_manager）、
`memory-facade`（memory/store.py + decay + cold/extractor）、`conversation-graph`（graph/）、
`session-window`（session/context.py）、`worldbook-matcher`（matcher + vector_index）、
`emotion-pipeline`（tools/emotion/）、`embodiment-timeline`（digital_human/ssml + viseme + models）、
`retrieval-fusion`（rag/retrieval/hybrid.py）、`api-skeleton`（api/ + config.py + main.py）

**builtin（15 项，可禁用/替换）**：

| # | 插件 id | 提供的能力 | 状态 |
| --- | --- | --- | --- |
| 1 | `llm-providers` | `llm`（mock / dashscope / siliconflow / openai-compatible） | ✅ 已收编 |
| 2 | `avatar-providers` | `digital_human`（local / xmov / gpt_sovits） | ✅ 已收编 |
| 3 | `memory-warm` | `warm_store`（memory / qdrant） | ✅ 已收编 |
| 4 | `memory-knowledge` | `knowledge_store`（memory / qdrant） | ✅ 已收编 |
| 5 | `session-store` | `session_store`（memory / sqlite） | ✅ 已收编 |
| 6 | `tools-builtin` | `tool`（4 个情感陪伴工具） | ✅ 已收编 |
| 7 | `mcp-bridge` | `mcp_manager`（MCP Client） | ✅ 已收编 |
| 8 | `embedding` | `embedding`（deterministic / cloud） | 待拆（现藏在 warm factory 内） |
| 9 | `tokenizer` | `tokenizer`（jieba / bigram） | ✅ 已收编 |
| 10 | `knowledge-parser` | `parser`（text / md / pdf / docx） | ✅ 已收编 |
| 11 | `knowledge-chunker` | `chunker` | 待抽接口 |
| 12 | `tts` | `tts`（从 digital_human 分出） | 待拆 |
| 13 | `st-format-adapter` | `datasource` / `prompt`（包装 st_compat） | 待包装 |
| 14 | `preset-ai-adaptation` | `prompt`（包装 adaptation） | 待包装 |
| 15 | `live2d-model-source` | `datasource`（模型来源） | ✅ 已实现 |
| 16 | **`tavern-bridge`** | `datasource` / `tool` / `settings`（酒馆只读接入） | ✅ 目录插件形态 |

> **粒度约定**：provider 类插件按**能力族**注册——一个工厂内含多个实现名
> （如 `llm-providers` 提供 mock/dashscope/siliconflow/openai-compatible），
> **不**按实现名拆成独立插件。拆分必须带来“可独立禁用”或“可独立替换”的实际价值。

### 9.11 实施顺序与进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| **P1a** | 插件宿主骨架（`capabilities` / `manifest` / `registry` / `context` / `manager` + 权限强制） | ✅ 已完成 |
| **P1b** | 收编既有 7 个扩展点（`builtin.py`，零重构） | ✅ 已完成 |
| **P1c** | 宿主接入（`create_app` 静态注册/发现 + lifespan 启停 + `/health` 暴露） | ✅ 已完成 |
| **P1d** | 前端能力中心（消费 `/health` 的插件数据；按层分组 + 状态 + 只读标记） | ✅ 已完成 |
| **P1d+** | 操作面：Schema 驱动的配置表单 + 启停 + 酒馆记忆导入（`PluginDetail` / `SchemaForm` / `TavernImportPanel`）；启用状态落盘 `data/plugins/<id>/state.json` | ✅ 已完成 |
| **P3** | `tavern-bridge` 插件（目录插件通路：PNG 角色卡 / 世界书 / 会话只读接入） | ✅ 已完成 |
| **P3b** | 插件管理 API（`/plugins/*`：列表 / 启停 / 配置读写） | ✅ 已完成 |
| **P4** | 跨会话记忆构建（`TavernMemoryImporter` 会话→记忆 + 导入 API） | ✅ 已完成 |
| **P2** | 抽接口拆分 builtin（`parser` / `tokenizer` / `emotion` 优先） | ✅ 已完成（parser → `knowledge/parser/`，tokenizer → `retrieval/tokenize/`，emotion 兜底策略留 core 内；均用 `__init__` 兼容层重导出，既有调用方零改动） |
| **P5** | Skill 体系（独立于插件，可随时插入） | ✅ 已完成（`backend/skills/` + `app/skills/` + `study_skill` + `/skills/*` + 前端面板） |
| **P6** | Live2D 模型来源插件 | ✅ 已完成（`app/digital_human/model_sources/` + `plugins/live2d-model-source/` + `GET /media/avatar/models/sources` + 前端「可获取的模型」） |
| **P7** | 插件市场（远期） | 待做 |
| **P8** | 目录重排（`app/core` / `app/builtin` 分层，见上方说明） | 待做 |

> **不做「为插件化而插件化」**：拆分必须同时产出「多一个实现」或「可禁用」的实际价值，
> 否则保留现状（工厂模式已足够）。每个 builtin 拆分都要有对应的测试。
