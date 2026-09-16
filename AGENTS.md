# HyPRA 项目约定（AGENTS.md）

> 约束本项目内的开发与协作。个人通用习惯见全局 `~/.pi/agent/AGENTS.md`；
> 两处冲突时，以本项目文件为准（项目级优先于全局级）。

## 1. 项目定位

HyPRA：打通「提示词架构」与「混合记忆」的情感陪伴 3D 交互系统（Web 应用，参赛项目）。
目标用户：高压快节奏生活中的现代人，提供符合幻想的 AI 情感陪伴，化解日常压力。
本项目借鉴 SillyTavern 的提示词工程与记忆体系**思想**，但底层代码 100% 原创。

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
        api/            # FastAPI 路由（chat / memory / media）
        prompts/        # 分层 System Prompt 模板
          persona/      #   人设预设（借鉴 SillyTavern 预设：预设与代码分离）
          state_vars/   #   动态状态变量定义（如 {{current_mood}}）
        worldbook/      # 世界书条目：JSON/YAML + 关键词/正则/向量触发规则
        memory/
          hot/          # 上下文滚动窗口（最近 N 轮）
          warm/         # WarmMemoryStore 接口 + Qdrant 实现（Embedding 入库/语义检索）
          cold/         # 结构化事实表：回复后 LLM 抽取 时间/地点/人物关系 + 摘要
        rag/            # 检索拼接与优先级逻辑（PromptManager）
        llm/            # 云端模型 API 封装
        tools/          # function calling 工具（情绪/记忆写入/TTS…）
      mcp/            # MCP Client：外部 MCP server 的工具接入 Agent 行动层
        digital_human/  # 魔珐星云 SDK 对接 + 媒体转码
      tests/            # pytest
    frontend/           # Next.js 应用
    docs/               # 参赛说明文档、演示脚本、设计文档
    README.md  LICENSE  .gitignore

## 4. 关键设计约束

- 提示词工程：System Prompt 分层（全局人设 / 状态变量 / 对话格式），预设内容与代码分离；
  世界书条目用 JSON/YAML，触发机制为关键词 + 正则 + 向量三重触发，注入位置分档并可配 token 预算。
- 记忆体系：热 / 温 / 冷三层；借鉴 SillyTavern 及其插件生态（官方 Data Bank/Summarize/
  Chat-vectorization 与社区记忆插件）的写入-召回机制。**具体实现按
  `docs/sillytavern-memory-design-reference.md` 的「8 条可落地参照」执行**，
  要点：回复后事件驱动抽取、摘要滚动增量合并、激活词/情绪加权、向量召回时间衰减、
  每「陪伴对象」独立 Qdrant collection 实现记忆隔离。
- **function calling + MCP 双主线（核心技术）**：LLM 回复强制走 function calling 结构化输出
  8 类情绪标签（正则仅作兜底），情绪标签同时驱动 3D 表情映射与记忆召回加权；并通过 MCP 协议
  把外部 MCP server 的工具桥接进 Agent 行动层（命名 `mcp__<服务器>__<工具>`），
  模型可自主调用外部能力「办事」（连接失败不影响启动，协议与传输由官方 SDK 负责）；
  文本 + 情绪标签 → SSML（含 KA 动作）→ 魔珐星云具身驱动 SDK 实时渲染，
  并实现渲染无关的驱动时间轴，可降级接入任意 3D/2D 模型。
- RAG 拼接优先级（PromptManager 固定顺序）：世界书触发 > 向量召回 > 结构化事实 > 摘要 > 滚动窗口。

## 5. 开发与验证

- 完成任一模块后必须运行验证：后端 `backend/tests`（pytest）；前端按 frontend 现有脚本（lint / build）。
- 新增大模型 / 新增第三方依赖前，先列出方案征求确认。
- 云端 key（百炼 / 硅基流动 / 魔珐星云 / Qdrant）一律放 `backend/.env`，不入库。
  双模式：评审交付用 docker compose 一键部署（本地 Qdrant），embedding/LLM 由评审在 .env 自填。

## 6. 合规与提交红线（参赛硬指标）

- 测试语料 / 世界书 / RAG 语料只使用自创或公有领域内容，无版权风险；
  借鉴社区插件（多为 AGPL-3.0）只取机制思想，不复制代码与提示词原文。
- README 与 LICENSE 必须声明：
  「借鉴 SillyTavern 架构思想，底层代码 100% 原创，不受 AGPL-3.0 协议传染」。
- 提交前检查：.env、node_modules、.venv、向量数据、生成的视频/语音等一律 gitignore，
  不提交无关文件。

## 7. 参赛文档产出

- docs/项目说明文档（按既定 PDF 大纲撰写）
- docs/演示脚本（3–5 分钟：长对话记忆召回、情绪变化→3D 表情联动、世界书触发）
- docs/sillytavern-memory-design-reference.md（记忆机制参照，已建立）
- README.md / LICENSE
