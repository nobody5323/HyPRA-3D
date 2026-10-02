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
- 记忆：**Qdrant 向量库（三形态同源：原生 Windows 二进制 / 本地 Docker / 云）**（温层向量库，经 `WarmMemoryStore` 接口抽象接入；本机开发用原生二进制，评审用 docker compose，也可切云）+ SQLite/JSON（冷层结构化）+ 内存滚动窗口（热层）
  - **版本纪律**：`qdrant-client` 与 server 的 **minor 差必须 ≤ 1**（官方要求，超出会出现
    `delete_collection` 报 500 等假错误）。因此 `backend/pyproject.toml` 钉 `>=1.19,<1.20`、
    `docker-compose.yml` 用 `v1.19.1`、本机 `_local/qdrant/qdrant.exe` 同版本 —— 改一处必须同改三处。
- 模型：Qwen2.5-7B 等开源模型，走国内托管 API（阿里百炼 / 硅基流动）
- 多模态：魔珐星云具身驱动 SDK（数字人实时渲染）+ SSML 播报指令（KA 动作）→ 前端 `speak()` 驱动
- 感知（输入多模态，**默认全关、逐项开启**，见 `docs/proactive-multimodal.md` §4）：
  语音识别（本地 faster-whisper）/ 图片理解（Qwen2.5-VL，走 OpenAI 兼容端点，零新依赖）
  / 桌面情景（只由 Electron 桌宠端采集：SMTC 正在听的歌 / 前台窗口进程名 / 空闲 / 时段）
  / **时间感**（服务端权威：日期 / 星期 / 时段，客户端上报优先、服务端兜底，Web 端也有）
  / **行踪与偏好画像**（按段记录「几点到几点在用什么程序」，只记程序名、保留 7 天、
  可一键清空；画像是从行踪**确定性聚合**的计数，不调模型）
  —— 感知源只产出**中性事实**，渲染集中在 `app/perception/context.py` 一处，
  汇集集中在 `app/perception/gather.py` 一处（四个来源，一个入口）
- 主动沟通（`docs/proactive-multimodal.md` §5）：触发器（定时 / 间隔 / 情景 / 行踪）→
  七道节制闸门（纯函数）→ **复用同一条 `chat_graph`** 生成 → SSE 推送到前端。
  ⚠️ 两条硬约定：① 触发器只出**意图**、不出文案；② 主动消息**不另起炉灶**生成
  （另写一套 = 人格漂移 + 两处维护）。
  八个触发器：`daily-greeting` / `idle-checkin` / `memory-followup` / `late-night` /
  `back-from-away` / `activity-shift` / `now-playing` / `low-battery`
- 前端：React / Next.js（对话 UI + 数字人视频播放），App Router
- 情绪输出：function calling 结构化输出为主，正则仅作兜底

## 3. 目录结构约定（新建代码须放入对应分层）

    backend/            # Python 后端
      app/
        core/           # core 能力清单（§9.10 的 9 项）：**只聚合导出，不含实现**；
                        #   是「哪些能力不可禁用」的单一事实来源（P8 轻档，未移动文件）
        plugins/        # 插件体系宿主（框架层，见 §9）：registry / manifest / context / manager
                        #   —— 与业务实现解耦，不反向依赖 app 内其它模块
        api/            # FastAPI 路由（chat / studio / knowledge / llm / media / avatar_models / st_presets）
        graph/          # LangGraph 编排：对话图 / 节点 / 状态
        llm/            # 模型接入：provider 工厂 + OpenAI 兼容实现 + profiles.yaml（模型预设）+ 推理参数
        memory/
          warm/         # 情景记忆：WarmMemoryStore 接口 + Qdrant/内存实现
          cold/         # 语义记忆：结构化事实表（三元组 + importance + 生命周期状态）
          knowledge/    # 个人记忆：用户上传语料（清洗/去重/解析/分块/混合检索）
          naming.py     # 作用域 id 规范化（冷层表名 / 温层与知识库 collection 名共用）：
                        #   Qdrant collection 名不接受冒号；易混 id（a-b / a_b）必须不碰撞
          store.py      # 记忆门面：聚合各层，产出「世界书之后、滚动窗口之前」的记忆块
                        #   注：**工作记忆（滚动窗口）不在本目录**，由 app/session/ 承载
        session/        # 会话与工作记忆：滚动窗口上下文 + 会话持久化（SQLite）
        events/         # 事件推送：进程内 pub/sub（SSE 的服务端一半）——
                        #   主动消息与感知提示都经它送到前端，见 docs/proactive-multimodal.md §5.1
                        #   ⚠️ **单进程假设**：多 worker 下消息会随机丢失，上多 worker 前必须换外部 pub/sub
        perception/     # 感知层：多模态输入 → 中性结构化事实 → 提示词（§4）
                        #   base/context/snapshot/busy + asr/（faster-whisper）
                        #   + vision/（Qwen2.5-VL）+ models.py（DesktopContext）
                        #   + clock.py（时间感：客户端优先、服务端兜底，Web 端也有）
                        #   + timeline.py（行踪：按段记录 / 空闲闭段 / 按天落
                        #     data/perception/*.jsonl / 保留期 / 一键清空 / 进程→类别）
                        #   + profile.py（偏好画像：从行踪确定性聚合，纯函数 + 缓存）
                        #   + gather.py（汇集四个来源，消费方唯一入口）
                        #   ⚠️ context.py 是**唯一渲染处**、gather.py 是**唯一汇集处**——
                        #     加一个来源只改这两处，不在调用点各拼一次
        proactive/      # 主动链路：触发 → 节制闸门 → 复用对话图 → 推送（§5）
                        #   triggers.py（八个触发器）/ gate.py（七道闸门，纯函数）
                        #   / runner.py（调度）/ state.py（节流状态落盘）/ prompt.py
        mcp/            # MCP **Client**：连接外部 MCP server，把工具接入 Agent 行动层
        tools/          # function calling 工具（情绪/记忆写入/内置工具注册表/技能学习）
        skills/         # Skill 体系：渐进式加载的能力说明（§9.6，独立于插件）
        worldbook/      # 世界书条目：YAML + 关键词/正则/向量三重触发匹配
        studio/         # 用户创作：自建角色卡 / 背景故事 / 世界书条目的存储与编辑（内置不可直接改，
                        #   可复制或删除——删除写用户侧隐藏清单，包内文件不改；数据落
                        #   backend/data/studio，设计见 docs/user-content-studio.md）
        prompts/        # 分层 System Prompt 模板（预设内容与代码分离）
          persona/      #   人设预设（借鉴 SillyTavern 预设：预设与代码分离）
          style/        #   文风预设（A/B 对比演示用）
          jailbreak/    #   叙事框架层（术语对齐 ST 的 jailbreak 槽位；**默认关闭**，
                        #   三段结构 fiction_frame / directive / boundary，
                        #   见 docs/jailbreak-layer.md）
          state_vars/   #   动态状态变量定义（如 {{current_mood}}）
          st_compat/    #   酒馆预设兼容：解析 / 宏 / 渲染 / 存储
          adaptation/   #   预设 AI 适配：检测 / 差异 / 规划（rules.yaml）
        rag/
          retrieval/    # 混合检索底座：分词 / BM25 / RRF 融合（情景记忆与知识库共用）
          prompt_manager.py  # 分层注入与 token 预算（PromptManager）+ 输出格式约束层（LAYER_FORMAT）
        tts/            # 语音：音色表 / 音频处理 / GPT-SoVITS 接入
        digital_human/  # 具身驱动：魔珐星云 SDK 对接 + SSML + viseme + 模型库
                        #   model_sources/ 模型来源（本机库 / 清单），见 §9.10 第 15 项
        config.py       # 全部可配置项（.env 读入，含本地/云双模式开关）
        paths.py        # 运行时路径解析：只读资源 / 可写数据 / 配置文件三者的**唯一**来源，
                        #   也是全仓唯一使用 `__file__` 的地方（打包感知，见 §5 末）
        main.py         # FastAPI 应用工厂 + 路由注册 + 生命周期（MCP 连接/断开）
      mcp_servers/      # 本项目**自带**的 MCP Server（独立进程，不属于 app 包，见其 README.md）
                        #   ⚠️ 与 app/mcp（Client）区分：这里是被连接的「外部服务」侧
      skills/           # 内置技能包（每技能一个 <id>/SKILL.md，入库随项目分发）
      plugins/          # 第一方插件（目录 + manifest.json 形态，见 §9.3）
        tavern-bridge/  #   酒馆数据只读接入（首个 datasource 插件）
        live2d-model-source/  #   数字人模型来源（第二个 datasource；只读，不代下载）
      scripts/          # 一次性运维/实验脚本（预设导入 / A-B / 探针），不参与运行时
      tests/            # pytest：目录与 app/ 分层一一对应（test_app / test_llm / test_memory / …）
      run_backend.py    # PyInstaller 打包入口（也承担 MCP 自举：`--mcp-server <script>`）
      backend.spec      # PyInstaller 规格（--onedir；只读资源随包，可写数据不打包）
      dist/  build/     # PyInstaller 产物与中间目录 —— gitignore
      THIRD_PARTY_LICENSES/  # 随包分发的第三方许可证（Qdrant 是 Apache-2.0）—— 合规要求见 §6
      data/             # 运行时数据（SQLite / 预设覆盖层 / logs/）—— gitignore
      media/            # 生成的语音与视频产物 —— gitignore
    frontend/           # Next.js 应用（App Router）
      app/              #   页面与布局
      components/       #   按域分组：chat / avatar / studio / settings
      hooks/            #   按域分组：avatar（具身与语音）/ chat（对话编排）
                        #     / perception（语音输入 useSpeechInput、发图 useImageShare）
      lib/              #   按域分组：api（接口封装 client.ts + 契约 types.ts
                        #     + 事件通道 events.ts）/ avatar（渲染与口型）
                        #     / chat（会话与本地偏好）/ studio / live2d（三态桥，路径不可动）
      vendor/cubism/    #   Cubism SDK（脚本落位，不入库）
      tests/            #   vitest
    desktop/            # Electron 桌面端（程序控制台 + 透明桌宠窗 + 托盘）
      src/main/         #   主进程：控制台窗/桌宠窗/穿透/托盘/设置/loopback 静态资源服务
                        #   以及 serviceManager.ts（代管 Qdrant、后端与 Web 前端；前端按需启动）
                        #     + servicePaths.ts（两形态路径）
                        #     + desktopContext.ts（桌面情景采集：SMTC / 前台窗口 / 空闲，
                        #       经 PowerShell -EncodedCommand，**零新依赖、不落脚本文件**）
      src/preload/      #   contextBridge 白名单（sandbox 下只能 require electron）
      src/renderer/pet/ #   桌宠窗（Vite 独立入口；经 `@/` 复用 frontend 的渲染器与 hooks）
                        #     useDesktopPerception.ts：把桌面情景上报后端（主进程不直连后端）
      src/renderer/console/ # 程序控制台（Vite 第二入口；复用 frontend/components + Tailwind）
      src/shared/       #   两端共享的 IPC 契约（禁 electron / react 依赖）
      scripts/          #   构建期脚本：verify-release-assets（资源边界校验）/ verify-bundled-runtime
                        #     （打包前置检查）/ install-live2d-model / make-app-icon
      release/          #   打包产物（portable + NSIS）—— gitignore
    docs/               # 参赛说明文档、演示脚本、设计文档
      plugin-development.md   # 插件接口公开说明（写插件的人只需要读这一篇）
      examples/plugin-hello/  # 可复制的最小插件（manifest.json + plugin.py + README）
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
  system 末尾另有**输出格式约束层**（`LAYER_FORMAT` = 形态 + 长度，**两种模式全局强制**）：
  ① **形态**——只输出说的话，不写旁白 / 动作 / 神情 / 环境 / 心理描写
  （`DIALOGUE_ONLY_RULE`，**恒开**，与 `prompts/adaptation/rules.yaml` 的 `target.dialogue_only` 同源）；
  ② **长度**——默认 100 字、`<=0` 关闭，经 `reply_char_limit` / 环境变量 `REPLY_CHAR_LIMIT` 调整。
  该层**不参与总量预算**，以免挤掉内容层；配合各文风预设 `max_tokens` 的收紧共同压住
  「写满额度」的倾向。酒馆模式不经过 PromptManager，由 `graph/nodes._assemble_with_st_preset`
  在渲染与 at_depth 插入之后追加同一条形态约束（文案同一来源，避免两处各写一遍）；
  `prompts/sanitize.py` 另做**输出兜底**（剥离星号动作、整行括号旁白、短括号插注，
  清理成空串时回退原文，不给用户一片空白）。
- **叙事框架层**（`LAYER_JAILBREAK`，术语对齐 ST 的 jailbreak 槽位，**默认关闭**）：
  声明「这段对话处在什么框架里」（虚构叙事 / 现实问答），与 persona（是谁）、
  style（怎么说话）正交。开启后放 system **最前**；关闭时该层为空串，提示词与
  不装本层时**逐字一致**（有回归测试守着）。预设内容与代码分离在
  `app/prompts/jailbreak/presets/`，每档必带 `<boundary>` 段（框架之外仍然成立的
  红线：现实问题如实回答 / 不生成真实可执行的伤害性步骤 / 危机信号优先）——
  这一段由测试强制存在，**新增或修改预设时不得删**。开关走
  `jailbreak_enabled` / `jailbreak_preset`（在 `RUNTIME_OVERRIDABLE_FIELDS` 白名单内，
  界面可切、保存即生效），单轮可用请求参数 `jailbreak_id` 覆盖（`"none"` = 强制关）。
  只作用于**内置分层路径**：ST 预设自带 jailbreak 槽位，叠一层会变成两套框架打架。
  三端均有入口（Web 端 / 控制台的 `components/settings/JailbreakSwitcher.tsx`、桌宠窗
  `pet/PresetPanel.tsx` 的原生下拉）；「未设置」与「用户明确关掉」必须区分显示——
  解析收敛在 `frontend/lib/chat/jailbreak.ts` 的 `resolveJailbreakId()`，三端共用。
  设计见 `docs/jailbreak-layer.md`。
- **切换器浮层必须用 `position: fixed`**（`components/settings/useAnchoredPopup.ts`）：
  控制台把面板包在 `Card overflow-hidden` 里，`absolute` 浮层会被裁成一条缝，
  表现为「下拉点不开」。四个切换器（人设 / 文风 / 模型预设 / 叙事框架）共用该 hook，
  有源码断言禁止退回 `absolute ... mt-1 w-full`。⚠️ 因此**面板外壳不得加
  `transform` / `filter` / `will-change` / `contain`**——那会让外壳变成 fixed 的包含块，
  裁剪重新生效。
- 桌面桌宠端（`desktop/`）：与 Web 端**共用后端与渲染层**，不重写对话/记忆；
  桌宠窗是 Vite 独立入口（不依赖 Next 服务），页面由主进程的 loopback 静态资源服务提供
  （前端资源引用是绝对路径 `/live2d`、`/portraits`，`file://` 下会 404），端口固定优先以
  保证 localStorage origin 稳定。主进程**不接触任何模型 API Key**，不代理业务请求。
  数字人的三档形象（魔珐 3D / Live2D / 立绘）在桌宠窗**同样可用**：魔珐 SDK 是
  **浏览器端本地渲染**且数字人**自带透明通道**（探针实测），与 `transparent: true` 的
  透明小窗天然契合；渲染容器按**竖屏 9:16** 取值（比例必须与魔珐控制台创建应用时
  选定的应用类型一致，实测同一个 appId 换比例会变形）。凭证按形态存后端
  （`data/avatar-credentials.json`：`web` 横屏给 Web 端、`pet` 竖屏给桌宠窗），
  由渲染层直接向后端取，主进程仍不接触密钥。
  **程序控制台**（`console.html`）是启动后的默认窗口（常规窗，关闭 = 隐藏），
  桌宠窗与 Web 端都由它在「模式启动」里选定后再启动（桌宠不再自动弹出）；
  控制台复用 `frontend/components/**`（已核实零 `next/*` 依赖），因此 desktop 接了 Tailwind
  （theme 从 `frontend/tailwind.config` import，单一来源）。
  模型构图校准（scale / offset / anchor）与「当前用哪个模型」均存**后端模型元数据**，两端口径一致；
  对话偏好（人设 / 文风 / 预设 / 酒馆预设）同样存后端（`data/chat-preferences.json`），
  优先级是「用户偏好 > 部署默认」。
  发布包不含模型/音频（白名单拷贝 + `scripts/verify-release-assets.mjs` 硬校验），
  用户自备模型放 `%APPDATA%/HyPRA/live2d/`。
  **安装包是自包含的**：`resources/` 内置 `backend/backend.exe`（PyInstaller onedir，自带 Python
  运行时）与 `qdrant/qdrant.exe`，目标机**不需要** Python / Node / Qdrant；主进程按 `app.isPackaged`
  区分两种形态（源码走项目根探测，安装包走 `resources/`），两边数据统一落 `%APPDATA%/HyPRA/`。
  打包前置检查是 `scripts/verify-bundled-runtime.mjs`（缺捆绑资源时打印可照抄的补救命令）。
  设计见 `docs/desktop-pet.md` §9.6–9.7。

## 5. 开发与验证

- 完成任一模块后必须运行验证：后端 `backend/tests`（pytest）；前端按 frontend 现有脚本（lint / build）；
  桌面端 `desktop` 下 `npm run typecheck` + `npm test` + `npm run build`（build 末尾会做发布物资源边界校验）；
  打安装包前先 `npm run verify:bundled`（检查捆绑资源是否就位）。
- 后端打包（安装包形态）：`cd backend && pyinstaller backend.spec --noconfirm` → `backend/dist/backend/`。
  依赖或资源文件变动后须重跑；产物与中间目录已被 gitignore，不进仓库。
  **冻结产物必须实测启动过**（`dist/backend/backend.exe` + `curl /health`）才算验完——
  静态分析收不全的依赖只有跑起来才暴露。已知两处见 `docs/proactive-multimodal.md` §4.3：
  `faster_whisper/assets/*.onnx`（VAD 模型，按包内相对路径加载）与
  `ctranslate2/ctranslate2.dll`（**普通 DLL 不是扩展模块**，PyInstaller 不会自动收）。
- **打包感知的两条分界线**（`backend/app/paths.py`，全仓唯一路径基准）：只读资源跟 `resource_root()`
  （冻结时是 `sys._MEIPASS`，开发时是 `backend/`）；可写数据跟 `data_root()`（冻结时是
  `%APPDATA%/HyPRA`，开发时是 `backend/`）。**新增资源 yaml 必须加进 `backend.spec` 的 datas**，
  否则打包后会读不到。
- 新增大模型 / 新增第三方依赖前，先列出方案征求确认。
  - **可选依赖 `asr`**（语音识别，`backend/pyproject.toml` 的 `[project.optional-dependencies]`）：
    刻意不进主依赖（拖 ctranslate2 / onnxruntime / av 三个原生扩展，约 70MB wheel）。
    ```bash
    # ⚠️ 清华镜像没有 faster-whisper，必须带官方源
    cd backend && ../.venv/Scripts/python.exe -m pip install \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --extra-index-url https://pypi.org/simple ".[asr]"
    # 模型权重不进仓库（.gitignore 已覆盖 backend/models/）也不随包分发，
    # 拉取要走镜像并关掉 Xet 传输后端（否则它绕过镜像直连 HF 报 401）：
    HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 ../.venv/Scripts/python.exe -c \
        "from huggingface_hub import snapshot_download as d; d('Systran/faster-whisper-small', local_dir='models/whisper/small')"
    ```
- **本地跑起来用根目录的一键启动**（`start.bat`，逻辑在 `start.ps1`）：默认目标 `desktop`
  （后端 + 程序控制台，桌宠/Web 由控制台「模式启动」里选），另有 `web` / `all` / `backend` /
  `frontend` / `console` / `docker` / `check` 与 `-Release`。它只做环境自检 + 端口占用复用 +
  就绪等待，不擅自装依赖；`.env` 用本机 Qdrant 时会先拉起 `_local/qdrant/qdrant.exe`
  （与桌面端 `serviceManager` 同一口径：cwd = exe 所在目录）。**改启动方式时两处要同步。**
- 云端 key（百炼 / 硅基流动 / 魔珐星云 / Qdrant）一律放 `backend/.env`，不入库；
  **安装包形态下的位置是 `%APPDATA%/HyPRA/.env`**（首次启动自动从 `.env.example` 生成，不覆盖已有文件）。
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
- docs/plugin-market.md（插件市场设计：索引/安装/回滚/安全防护/信赖边界，已建立，**设计稿未实现**）
- docs/proactive-multimodal.md（多模态感知与主动沟通：参照 FaustBot 的取舍、三条链路、
  七道节制闸门、隐私红线、时间感与行踪（§4.8 / §4.9）、实施进度，**P1–P5 已实现**）
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
  - 实现：`session.mode` 字段（SQLite 加列 + 存量迁移）+ `app/session/mode.py`
    的 `resolve_mode()`。**优先级：显式请求 `mode` > 由 `st_preset_id` 推断 > 会话既有**。
    第二级只是**没选过模式时的兜底**：引入显式开关之前，「选了酒馆预设」是酒馆模式的
    唯一信号，推断成 `tavern` 才不至于让酒馆世界书突然全召不回来。请求带了 `mode`
    就轮不到它。
  - **模式与酒馆预设正交，不要绑成因果**：模式管**记忆作用域与召回范围**，
    `st_preset_id` 管**提示词走哪条组装路径**。「桌宠模式 + 挂着酒馆预设」是
    **合法组合**（提示词按酒馆预设组装，但酒馆世界书不参与召回）——用户明确要过
    「桌宠模式也可以用酒馆预设」，别把它当错配去"修"。
  - **显式开关已落地**（三个界面共用一份偏好 `chat-preferences.json` 的 `mode` 字段）：
    桌宠窗在右键菜单（`menuitemradio` 两项，各带一句「召不召回酒馆世界书」），
    Web 端在能力中心紧上方的「交互模式」栏（默认展开）。两处都调
    `useChatSession.setMode`，它按「会话级属性」的约定**开始新对话**。
    没选过时不写 `mode`（传 null），仍由第二级按酒馆预设推断。
  - 模式当前的**行为落点只有一个**：知识检索是否纳入酒馆来源作用域（见 §8.2）。
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
- **酒馆世界书（外部来源）走独立作用域，且只在 `tavern` 模式召回**：全局书 `tavern`、
  角色内嵌书 `tavern:{persona_id}`。作用域键的唯一来源是
  `app/memory/knowledge/scopes.py`（写入侧与检索侧共用同一套函数——两边各算各的键，
  内容就会挂在一个永远匹配不上的作用域上）。`companion` 模式**一个 `tavern` 作用域都不查**：
  否则桌宠模式的内置人设会「知道」另一部作品的角色设定，实测会被用户读成「召回坏了」。
  注：这与上一条（`tavern` 不注入用户上传语料）方向相反、互不冲突——隔离的是**来源**，不是模式。
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
4. 双模式切换（前端 UI + 编排分派）——**已落地**（§8.1 / §8.2：桌宠右键菜单 +
   Web「交互模式」栏；酒馆来源只在 `tavern` 模式召回）。
   注：**模式不参与分派组装策略**，这是刻意的——两者正交，见 §8.1
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

**插件由用户自己写，宿主不生成插件代码**（接口说明
[`docs/plugin-development.md`](docs/plugin-development.md)，可复制的示例
[`docs/examples/plugin-hello/`](docs/examples/plugin-hello/)）：

| 环节 | 落地 |
| --- | --- |
| 公开接口面（插件唯一稳定入口） | `app/plugins/sdk.py`：`PluginContext` + 中性契约 + `ToolSpec` + manifest 工具函数；其余 `app.*` 视为内部实现 |
| 入口契约 | 入口文件（`manifest.entry`）里必须有 `build(ctx) -> dict`，返回 `tools` / `datasources` / `providers` / `prompt_fragments` / `hooks` / `startup` / `shutdown`；**会被重复调用，必须幂等** |
| 零拷贝接入（开发期） | `PLUGIN_EXTRA_DIRS`（逗号分隔）→ `PluginManager.plugin_dirs`；宿主直接扫插件所在目录，不碰源文件 |
| 导入接入 | `POST /plugins/import`（`app/plugins/loader.py`）：校验 → 暂存 → 整体替换，失败回滚；只收 `third-party` 层 |
| 发现 | `POST /plugins/reload` 只读 manifest 并登记，**不重跑**已加载插件的代码（不是热重载，§9.7） |
| 界面 | 能力中心「添加插件」（`PluginDevPanel.tsx`）：接口在哪、插件放哪、两条接入路径、重新扫描 |

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

**声明式配置怎么生效（✅ 已实现）**——`settings_schema` 的键名与宿主 `Settings`
字段同名时，宿主在 `PluginManager.setup_all()` 把它写进**运行时覆盖层**
（`app/config.py`，语义 = 界面设置 > .env），因此界面上填完保存就生效，
不必改 .env、不必重启；留空 = 沿用 .env。三条硬约定：

| 约定 | 落地 |
| --- | --- |
| 只能覆盖宿主**开放**的字段（白名单 `RUNTIME_OVERRIDABLE_FIELDS`） | `settings.json` 是用户可手改的文件，不能让它任意改写宿主配置；越权字段名忽略并告警 |
| 密钥字段（`format: "password"`）**永不回传明文** | GET 只回 `secrets_set`（有没有）；PUT 留空 = 沿用、`null` = 清除（口径同 `/llm/config`） |
| 配置真的变了才重建消费方单例 | 改一个没有运行时配置的插件不该白白重建 embedding 客户端 |

桥接层是 `app/plugins/settings_runtime.py`（`registry`/`manifest`/`context` 仍与业务无关）；
对话模型**不走这条路**——它有自己的 `data/llm_runtime.json` + `/llm/config`（含连通性测试）。
首个使用者：`embedding`（向量化 API）与 `tts`（GPT-SoVITS 服务地址与音色）两个内置插件。

### 9.6 Skill 体系（借鉴 Alife `SkillService`）—— ✅ 已实现

- 位置：`backend/skills/<id>/SKILL.md`（内置）+ `backend/data/skills/`（用户添加）
- `SKILL.md` = YAML frontmatter（`name` / `description` / `when_to_use`）+ Markdown 正文
- **渐进式注入**：常驻只注 `name: description` 清单（几十 token）；模型调 `study_skill(name)` 才把正文吐回上下文
- 禁用：`SKILLS_DISABLED` + 界面启停（**落盘** `data/skills.json`，与插件同理由）
- 删除（与启停是两件事）：自建技能删文件，内置技能只写隐藏清单（见下）
- 与 pi 的 skills 同范式，格式可对齐

**删除的两种语义**（`SkillRegistry.delete` 一处定，界面只如实说明）：

| 来源 | 删除做了什么 | 可否恢复 |
| --- | --- | --- |
| 自建（`USER_SKILLS_DIR`） | 删掉用户自己的文件（目录形整包删，含附件） | 否（要恢复就是再写一份） |
| 内置（`backend/skills/`） | 只写用户侧**隐藏清单**，包内文件一字未改（§9.12 硬规则 2） | 是，`POST /skills/{id}/restore` |

两条路都落 `data/skills.json`（`deleted` 键，为空时不写），因此重启与「重新扫描」
都不会让删掉的东西冒回来。清单接口**含隐藏项**（`deleted: true`）——
界面要靠它单列一块「已隐藏」并提供恢复入口，否则用户删掉内置技能就再也找不回来；
而对话侧的常驻清单与 `study_skill` 走 `all()` / `enabled()`，隐藏项对模型等于不存在。
物理删除只经 `skills/writer.py: delete_skill`，且**只认落在用户目录里的路径**
（loader 记下的路径不受 id 校验约束，越界即拒删）。

落地要点（`app/skills/` + `app/tools/skills.py` + `app/api/skills.py`）：

| 环节 | 位置 |
| --- | --- |
| 解析与扫描 | `skills/loader.py`（容错 BOM / 缺 frontmatter / YAML 写坏；目录形与单文件形都收） |
| 装载与启停 | `skills/registry.py`（内置优先于用户同名；全局单例，`create_app` 装载） |
| 删除与恢复 | `skills/registry.py: delete/restore` + `skills/writer.py: delete_skill`（只删用户目录） |
| 常驻清单 | `registry.catalog_text()` → `PromptManager` 的 skills 层（priority 75） |
| 按需正文 | `tools/skills.py: study_skill`（**当轮生效**，不常驻上下文）；空技能库时不注册 |
| 管理接口 | `GET /skills`、`GET /skills/{id}`、`POST /skills`（新建，§9.12）、`POST /skills/ai-draft`（模型生成草稿）、`POST /skills/{id}/enabled`、`DELETE /skills/{id}`、`POST /skills/{id}/restore`、`POST /skills/reload` |
| 前端 | `components/settings/SkillPanel.tsx`（清单来自 `/health`，正文展开时才拉；删除二次确认） |

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

详细设计（索引格式 / 安装流程 / 安全防护 / 信赖边界 / 分阶段实施，**尚未实现**）：
[`docs/plugin-market.md`](docs/plugin-market.md)。

### 9.10 全部插件化清单

**core（9 项，不可禁用）**：`prompt-assembly`（assemble/renderer/sanitize + prompt_manager）、
`memory-facade`（memory/store.py + decay + cold/extractor）、`conversation-graph`（graph/）、
`session-window`（session/context.py）、`worldbook-matcher`（matcher + vector_index）、
`emotion-pipeline`（tools/emotion/）、`embodiment-timeline`（digital_human/ssml + viseme + models）、
`retrieval-fusion`（rag/retrieval/hybrid.py）、`api-skeleton`（api/ + config.py + main.py）

**builtin（20 项，可禁用/替换）**：

| # | 插件 id | 提供的能力 | 状态 |
| --- | --- | --- | --- |
| 1 | `llm-providers` | `llm`（mock / dashscope / siliconflow / openai-compatible） | ✅ 已收编 |
| 2 | `avatar-providers` | `digital_human`（local / xmov / gpt_sovits） | ✅ 已收编 |
| 3 | `memory-warm` | `warm_store`（memory / qdrant） | ✅ 已收编 |
| 4 | `memory-knowledge` | `knowledge_store`（memory / qdrant） | ✅ 已收编 |
| 5 | `session-store` | `session_store`（memory / sqlite） | ✅ 已收编 |
| 6 | `tools-builtin` | `tool`（4 个情感陪伴工具） | ✅ 已收编 |
| 7 | `mcp-bridge` | `mcp_manager`（MCP Client） | ✅ 已收编 |
| 8 | `embedding` | `embedding`（deterministic / dashscope / siliconflow / openai-compatible） | ✅ 已收编 |
| 9 | `tokenizer` | `tokenizer`（jieba / bigram） | ✅ 已收编 |
| 10 | `knowledge-parser` | `parser`（text / md / pdf / docx） | ✅ 已收编 |
| 11 | `knowledge-chunker` | `chunker`（semantic / plain） | ✅ 已收编 |
| 12 | `tts` | `tts`（gpt_sovits / none，从 digital_human 分出） | ✅ 已收编 |
| 13 | ~~`st-format-adapter`~~ | — | ⛔ **不单独成插件**：`st_compat` 属 core 的 `prompt-assembly`（渲染就是提示词组装的一部分），再包一层 builtin 会让同一份东西既在 core 又在 builtin；解析部分保留在原处 |
| 14 | `preset-ai-adaptation` | `preset_adaptation`（按**动作名**寻址：detect / plan / build_diff / load_rules） | ✅ 已收编 |
| 15 | `live2d-model-source` | `datasource`（模型来源） | ✅ 已实现 |
| 16 | **`tavern-bridge`** | `datasource` / `tool` / `settings`（酒馆只读接入） | ✅ 目录插件形态 |
| 17 | `asr` | `asr`（faster-whisper / none）—— 语音识别，见 §4.3 | ✅ 已实现 |
| 18 | `vision` | `vision`（Qwen2.5-VL / none，走 OpenAI 兼容端点，零新依赖）—— 图片理解，见 §4.4 | ✅ 已实现 |
| 19 | **`perception-ambient`** | 无 provider（能力是**采集与聚合**）；注册它是为了拿到声明式配置表单——时间 / 行踪 / 画像的开关（记多久、记不记标题、要不要画像）必须**在界面上可见可改**，那正是 §4.9 隐私红线的落地方式 | ✅ 已实现 |
| 20 | `proactive` | 无 provider（能力是**编排**）；注册它是为了拿到声明式配置表单，让七道闸门的参数可在界面上调 | ✅ 已实现 |

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
| **P2** | 抽接口拆分 builtin（`parser` / `tokenizer` / `emotion` 优先） | ✅ 已完成（两批：① `parser` → `knowledge/parser/`、`tokenizer` → `retrieval/tokenize/`、`emotion` 兜底留 core；② `chunker` → `knowledge/chunker/`、`embedding` / `tts` / `preset-ai-adaptation` 收编。均用 `__init__` 兼容层重导出，既有调用方零改动） |
| **P5** | Skill 体系（独立于插件，可随时插入） | ✅ 已完成（`backend/skills/` + `app/skills/` + `study_skill` + `/skills/*` + 前端面板） |
| **P6** | Live2D 模型来源插件 | ✅ 已完成（`app/digital_human/model_sources/` + `plugins/live2d-model-source/` + `GET /media/avatar/models/sources` + 前端「可获取的模型」） |
| **P7** | 插件市场（远期） | 待做 |
| **P8** | 目录重排（`app/core` / `app/builtin` 分层） | ✅ 已完成（**轻档**：只新增 `app/core/__init__.py` 声明 9 项 core 的公开符号，**不移动任何文件**；后续如需物理重排，边界已经写死在那儿，测试会卡住漂移） |
| **P9** | 声明式配置的**生效通路**：运行时覆盖层（`app/config.py`）+ 桥（`app/plugins/settings_runtime.py`，含密钥只进不出）+ 能力中心里配 embedding / TTS | ✅ 已完成（界面设置 > .env；密钥字段只回 `secrets_set`；改动才重建单例） |
| **P10** | LLM 辅助创作：人设补全 / 写技能（§9.12）。**插件不在其中**——生成即执行会把「用户自己审阅」挤掉，改为用户手写 | ✅ 已完成（`app/llm/authoring.py` + `app/skills/writer.py` + 两处界面入口） |
| **P11** | 插件接口公开化：`app/plugins/sdk.py`（稳定面）+ `docs/plugin-development.md` + `docs/examples/plugin-hello/` + `PLUGIN_EXTRA_DIRS`（零拷贝）+ `POST /plugins/import`（导入） | ✅ 已完成 |

> **不做「为插件化而插件化」**：拆分必须同时产出「多一个实现」或「可禁用」的实际价值，
> 否则保留现状（工厂模式已足够）。每个 builtin 拆分都要有对应的测试。
>
> **§9.11 P2 收尾补充说明**（表中 13 项已收编，第 13 项按 C 档不做）：
>
> - **注册表是能力账本，不是运行时唯一通路**。`tokenizer` / `parser` / `chunker`
>   被禁用时，模块级入口（`split_text()` / `parse()` / `tokenize()`）照常工作——
>   这是**刻意**的（见 `test_disabling_split_plugins_does_not_break_retrieval`），
>   否则禁一个插件会把检索链路整个打断。
> - 同理，这些能力的工厂**不读新配置开关**：`KNOWLEDGE_SEMANTIC_CHUNKING` 已经表达了
>   「要不要语义切分」，再加 `KNOWLEDGE_CHUNKER` 就是两个开关说同一件事，迟早漂移。
> - `tts` 是唯一**被真实消费**的一项：`GptSovitsDigitalHumanProvider` 经 `TtsProvider`
>   接口取音频，换 TTS 不必改驱动；`DIGITAL_HUMAN_PROVIDER=gpt_sovits` 保留为兼容入口。
> - `preset-ai-adaptation` 按**动作名**而不是实现名寻址：适配天然是一组动作，
>   硬造一个只有一个实现的策略接口，只是把「包装」写成「架构」。

### 9.12 LLM 辅助创作（人设 / 技能）—— ✅ 已实现

用**当前对话模型**生成内容草稿：人设（角色卡）与技能（`SKILL.md`）。默认用同一个
provider（界面的「对话模型」或 `.env`），**不新增模型开关**——否则「配了模型却生成不了」
会变成第二个要排查的地方。

| 入口 | 生成什么 | 落盘走哪条路 |
| --- | --- | --- |
| 创作工坊 → 角色卡编辑器「AI 补全」 | 人设字段（名称 / 简介 / 正文 / 背景 / 变量） | 既有的 `POST|PUT /chat/studio/personas`（**只填表单，不自动建角色**） |
| 能力中心 / 技能面板「AI 编写技能」 | `id` / 名称 / 何时用 / Markdown 正文 | `app/skills/writer.py` → `USER_SKILLS_DIR/<id>/SKILL.md` |

**三条硬规则**（都由宿主强制，不靠界面自觉）：

1. **草稿先行**：`POST .../ai-draft` 一律**只生成、不落盘**（同 `ai-adapt` 的 `dry_run` 语义），
   落盘是各自的 writer 在用户确认后才做的事——写路径只有一条；
2. **只写用户目录**：`data/skills`、`data/studio`；
   `backend/skills/`、`backend/plugins/` 是随包分发的第一方内容，运行时永不改写
   （删除内置技能同理：只写用户侧隐藏清单，见 §9.6）；
3. **写前校验、写后回读**：id 合法性、SKILL.md 能被 loader 读回；回读失败就删掉，不留半成品。

**插件不在这条链上**（P11 起改为用户手写）。理由：插件是会被宿主 `import` 并执行的代码，
让模型现写现装等于把「用户自己审阅」这一步挤掉——而这一步恰恰是唯一真正的防线
（§8.5 / §9.7 已明确没有沙箱、不做签名）。所以插件走的是另一条路：

- 接口说明 `docs/plugin-development.md`，可复制的示例 `docs/examples/plugin-hello/`；
- 公开接口面 `app/plugins/sdk.py`（插件只 import 它，其余 `app.*` 是内部实现）；
- 接入走 `PLUGIN_EXTRA_DIRS`（零拷贝）或 `POST /plugins/import`（导入），见 §9.3。

实现位置：`app/llm/authoring.py`（模型解析 / 容错 JSON / 两类草稿模型与清洗 / 重试）、
提示词 `app/prompts/authoring/*.md`（与代码分离；**新增文件必须同步 `backend.spec` 的 datas**）、
`app/skills/writer.py`、
界面 `AiBriefForm` + `PersonaStudio` / `SkillPanel`（+ 控制台「人设与文风」的跳转入口）。

**模型是本地占位实现（mock）时直接拒绝**（400 并提示去配对话模型）：它的回复是写死的文本，
拿它生成只会得到一堆看着像样、实际不能用的东西。
