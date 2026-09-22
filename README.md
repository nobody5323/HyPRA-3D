# HyPRA-3D：打通提示词架构与混合记忆的情感陪伴 3D 交互系统

[![CI](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml/badge.svg)](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Tests](https://img.shields.io/badge/tests-802%20passed-brightgreen)

> 在高速发展的网络情感、日益破碎化的日常生活中，给现代人一个完全符合其幻想的 AI 陪聊助手，
> 化解当代人各方面的压力。

HyPRA 借鉴 [SillyTavern](https://github.com/SillyTavern/SillyTavern) 的提示词工程与记忆体系**思想**，
将「分层提示词架构 + 混合记忆引擎 + 情绪识别 + 3D 数字人驱动」打通为一个完整的情感陪伴 Web 应用。

## ✨ 功能亮点

- **分层提示词（System Prompt）架构**：全局人设 / 动态状态变量（如 `{{current_mood}}`）/ 对话格式分层设计；
  预设内容与代码分离。
- **世界书（World Info）动态注入**：JSON/YAML 条目 + **关键词 / 正则 / 语义向量三通道**触发
  （任一命中即注入），按 priority 与 token 预算拼入上下文；向量通道复用 embedding 做语义匹配，
  能捕捉关键词覆盖不到的同义改写（如「翻来覆去睡不着」→「深夜倾听模式」条目）。
- **混合记忆引擎（认知心理学四层框架）**——设计见 [`docs/memory-architecture.md`](docs/memory-architecture.md)：
  - **工作记忆**：上下文滚动窗口，逐字保留最近 N 轮；
  - **情景记忆**：Qdrant 向量库（本地 Docker / 云双模式），每轮对话向量入库；召回走
    **BM25 + 稠密向量 → RRF 融合**的混合检索，再施加时间衰减与情绪加权（即 **Memory RAG**）；
  - **语义记忆**：SQLite 结构化事实表（三元组 + importance + 生命周期状态），
    回复后事件驱动抽取写入，按重要性常驻；
  - **个人记忆**：用户上传的私人语料（txt / md / PDF / Word），经**清洗去噪 →
    三级去重（文件 MD5 / 内容 MD5 / MinHash）→ 语义分块**后入库，与情景记忆
    **共用同一套混合检索底座**，但**无时间衰减、无情绪加权**——知识不老化、是客观的。
- **RAG + 推理编排**：LangChain / LangGraph 组装。检索分**两条线**，共用同一套 embedding 与 prompt
  注入层，最终按固定优先级拼装：
  **① 记忆检索（Memory RAG）**＝混合检索（BM25 + 向量 + RRF）的情景回忆 + 语义事实；
  **② 知识检索（World Info）**＝世界书三通道触发；
  **③ 个人语料检索**＝用户上传资料的混合检索。
  注入顺序：世界书触发 > 个人记忆 > 情景记忆 > 语义事实 > 滚动窗口。
- **全链路 function calling + MCP 工具生态**：LLM 回复强制走 **function calling** 结构化输出
  **8 类情绪标签**（正则仅作兜底）→ 情绪驱动 **3D 表情映射**与**记忆召回加权**；并通过
  **MCP 协议**把外部 MCP server 的工具接入 Agent 行动层（`mcp__<服务器>__<工具>`），
  模型可自主调用外部能力「办事」；文本 + 情绪标签 → **SSML 播报指令**（含 KA 动作）→
  魔珐星云具身驱动 SDK 实时渲染，提供**渲染无关的驱动时间轴**，可降级接入任意 3D/2D 模型。
- **酒馆（SillyTavern）预设兼容**：可直接导入你在酒馆里用的 **Chat Completion 预设 JSON**，按它的
  条目顺序、启用开关、注入深度（含 In-Chat `depth`）、`use_sysprompt` 覆盖、扩展注入槽与
  `{{char}}` / `{{getvar::}}` 等宏组装提示词；前端提供**参数面板 + 条目编辑器**
  （拖拽/↑↓ 排序、逐条开关、展开改正文/depth/role、记忆注入落点、导出回酒馆），
  并显式列出「该预设哪些部分在本项目不生效」而不静默失败。
  契约、组装语义与合规边界见 [`docs/st-preset-compat.md`](docs/st-preset-compat.md)。

## 🧠 设计参照与文档

| 文档 | 内容 |
|---|---|
| [`docs/competition-gap-analysis.md`](docs/competition-gap-analysis.md) | **赛题差距分析**（评审维度对照与行动优先级） |
| [`docs/why-embodied-avatar.md`](docs/why-embodied-avatar.md) | **不可替代性论证**（为何情感陪伴需要具身数字人） |
| [`docs/frontend-plan.md`](docs/frontend-plan.md) | 前端方案（Next.js + 魔珐 SDK + 具身状态机） |
| [`docs/frontend-avatar-integration.md`](docs/frontend-avatar-integration.md) | 前端 SDK 接入指南（含 speak 注意事项与 FAQ） |
| [`docs/deployment.md`](docs/deployment.md) | 部署说明（Docker Compose 一键部署 / 开发模式） |
| [`docs/sillytavern-memory-design-reference.md`](docs/sillytavern-memory-design-reference.md) | 记忆与提示词机制的设计参照调研 |
| [`docs/st-preset-compat.md`](docs/st-preset-compat.md) | **酒馆预设兼容契约**（字段映射 / 组装语义 / 宏 / 不支持清单 / 合规边界） |
| [`AGENTS.md`](AGENTS.md) | 项目开发约定（架构分层、红线、验证要求） |

## 🚀 快速开始

开发模式（零云端依赖可跑通链路）：

```bash
# 后端
cd backend
python -m venv ../.venv && ../.venv/Scripts/pip install -e ".[dev]"
cp .env.example .env    # 默认 mock LLM + 本地 embedding，无需任何 key
../.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
# 访问 http://localhost:8000/docs 调 POST /chat 即可对话
```

```bash
# 前端（另开一个终端）
cd frontend
npm install
npm run dev             # 访问 http://localhost:3000
# 数字人密钥可在页面右上角「数字人设置」里直接填写（即时生效，无需重新构建）；
# 未填 / 初始化失败 → 自动降级为浏览器原生语音，对话、字幕、情绪均不受影响
```

评审模式（docker compose 一键部署，见 [`docs/deployment.md`](docs/deployment.md)）。

> **关于 Embedding（影响语义检索效果）**
> 不填 key 时用本地确定性实现（零依赖，可跑通全链路），但它是字符 n-gram 哈希：
> 记忆向量召回与**世界书向量通道**会降级为字面级匹配（关键词 / 正则通道不受影响）。
> 填入 `EMBEDDING_PROVIDER` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`
> 即启用真实语义检索。`EMBEDDING_DIM` 须与模型实际输出一致（`Qwen/Qwen3-Embedding-8B` = 4096、
> `text-embedding-v3` = 1024），不符时首次请求会直接报错并告知正确值。
> 世界书条目的 `vector_threshold`（默认 0.7）须按所用模型校准——不同模型相似度基线差异很大，
> 实测数据见 `backend/app/worldbook/models.py`。

## 🗂 目录结构

    backend/     Python 后端（FastAPI + LangChain/LangGraph + Qdrant + 魔珐星云驱动）
    frontend/    Next.js 前端（对话 UI + 情绪/记忆可见性 + 个人记忆上传 + 魔珐具身数字人）
    docs/        设计文档、部署说明、参赛说明、演示脚本
    AGENTS.md    项目开发约定
    LICENSE

## 📄 合规声明

> **本项目借鉴 SillyTavern 架构思想，但底层代码 100% 原创，不受 AGPL-3.0 协议传染。**
> 项目内测试语料 / 世界书 / RAG 语料仅使用自创或公有领域内容。

**关于「酒馆预设兼容」的合规边界**（详见 [`docs/st-preset-compat.md`](docs/st-preset-compat.md) §1）：

| 类别 | 处理 |
|---|---|
| **发行内容**（仓库源码 / 内置预设 / 镜像 / 文档） | **零** SillyTavern 或社区预设的提示词原文；内置预设全部自写 |
| **运行时用户数据**（用户自己导入的预设 JSON） | 存本机 `backend/data/presets/`（已 gitignore），只解析与渲染，**不入库、不随发行物分发** |

实现上另有两道防护：导入时**剥离且不保存**端点/密钥类字段（避免误存用户凭证）；
导入的预设文件**只读**，界面编辑写入独立的覆盖层文件（可随时「恢复导入时」）。

## 📌 路线图

- [x] M1 提示词架构（分层 System Prompt + 世界书三通道触发）
- [x] M2 混合记忆引擎（热 / 温 / 冷三层）
- [x] M3 RAG + LangGraph 推理编排
- [x] M4 情绪识别 + 工具调用（function calling + MCP）
- [x] M5 数字人驱动**指令层**（SSML 播报 + KA 动作 + 渲染无关时间轴）
- [x] M6 前端 + 魔珐 SDK 接入（对话 UI / 流式字幕 / 具身状态机 / 降级与退避重连）
- [x] M6.5 前端对接全部后端接口（个人记忆上传、人设与文风清单、行动层状态、分段播报）
- [x] M6.7 酒馆（SillyTavern）预设兼容（导入 / 组装语义 / 宏 / 前端参数面板与条目编辑器）
- [ ] M7 100+ 轮长对话压测与记忆调优
- [ ] M8 参赛文档与演示视频

> M6/M6.5 完成的是**代码与接口对齐**：真实渲染效果依赖魔珐密钥与积分
> （页面「数字人设置」或 `frontend/.env.local` 填写后即可验证，未填则走浏览器语音降级）。
> 缺口清单与优先级见 [`docs/competition-gap-analysis.md`](docs/competition-gap-analysis.md)。

## 📄 许可

本项目基于 [Apache License 2.0](LICENSE) 开源（Copyright 2026 nobody5323）。
**声明：本项目借鉴 SillyTavern 架构思想，底层代码 100% 原创，不受 AGPL-3.0 协议传染。**
