# HyPRA-3D：打通提示词架构与混合记忆的情感陪伴 3D 交互系统

[![CI](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml/badge.svg)](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Tests](https://img.shields.io/badge/tests-286%20passed-brightgreen)

> 在高速发展的网络情感、日益破碎化的日常生活中，给现代人一个完全符合其幻想的 AI 陪聊助手，
> 化解当代人各方面的压力。

HyPRA 借鉴 [SillyTavern](https://github.com/SillyTavern/SillyTavern) 的提示词工程与记忆体系**思想**，
将「分层提示词架构 + 混合记忆引擎 + 情绪识别 + 3D 数字人驱动」打通为一个完整的情感陪伴 Web 应用。

## ✨ 功能亮点

- **分层提示词（System Prompt）架构**：全局人设 / 动态状态变量（如 `{{current_mood}}`）/ 对话格式分层设计；
  预设内容与代码分离。
- **世界书（World Info）动态注入**：JSON/YAML 条目 + 关键词 / 正则 / 向量三重触发机制，按优先级拼入上下文。
- **混合记忆引擎（热 / 温 / 冷三层）**：
  - 热层：上下文滚动窗口，保留最近 N 轮；
  - 温层：Qdrant 向量库（本地 Docker / 云双模式），对话 Embedding 入库与语义检索（含时间衰减）；
  - 冷层：SQLite/JSON 结构化事实表，回复后由 LLM 自动抽取并更新关键事实（时间、地点、人物关系）+ 增量摘要。
- **RAG + 推理编排**：LangChain / LangGraph 组装——世界书触发 > 向量召回 > 结构化事实 > 摘要 > 滚动窗口。
- **全链路 function calling + MCP 工具生态**：LLM 回复强制走 **function calling** 结构化输出
  **8 类情绪标签**（正则仅作兜底）→ 情绪驱动 **3D 表情映射**与**记忆召回加权**；并通过
  **MCP 协议**把外部 MCP server 的工具接入 Agent 行动层（`mcp__<服务器>__<工具>`），
  模型可自主调用外部能力「办事」；文本 + 情绪标签 → **SSML 播报指令**（含 KA 动作）→
  魔珐星云具身驱动 SDK 实时渲染，提供**渲染无关的驱动时间轴**，可降级接入任意 3D/2D 模型。

## 🧠 设计参照与文档

| 文档 | 内容 |
|---|---|
| [`docs/competition-gap-analysis.md`](docs/competition-gap-analysis.md) | **赛题差距分析**（评审维度对照与行动优先级） |
| [`docs/why-embodied-avatar.md`](docs/why-embodied-avatar.md) | **不可替代性论证**（为何情感陪伴需要具身数字人） |
| [`docs/frontend-plan.md`](docs/frontend-plan.md) | 前端方案（Next.js + 魔珐 SDK + 具身状态机） |
| [`docs/frontend-avatar-integration.md`](docs/frontend-avatar-integration.md) | 前端 SDK 接入指南（含 speak 注意事项与 FAQ） |
| [`docs/deployment.md`](docs/deployment.md) | 部署说明（Docker Compose 一键部署 / 开发模式） |
| [`docs/sillytavern-memory-design-reference.md`](docs/sillytavern-memory-design-reference.md) | 记忆与提示词机制的设计参照调研 |
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

评审模式（docker compose 一键部署，见 [`docs/deployment.md`](docs/deployment.md)）。

## 🗂 目录结构

    backend/     Python 后端（FastAPI + LangChain/LangGraph + Qdrant + 魔珐星云驱动）
    frontend/    Next.js 前端（对话 UI + 数字人视频播放）
    docs/        设计文档、部署说明、参赛说明、演示脚本
    AGENTS.md    项目开发约定
    LICENSE

## 📄 合规声明

> **本项目借鉴 SillyTavern 架构思想，但底层代码 100% 原创，不受 AGPL-3.0 协议传染。**
> 项目内测试语料 / 世界书 / RAG 语料仅使用自创或公有领域内容。

## 📌 路线图

- [ ] M1 提示词架构（分层 System Prompt + 世界书）
- [ ] M2 混合记忆引擎（热 / 温 / 冷三层）
- [ ] M3 RAG + LangGraph 推理编排
- [ ] M4 情绪识别 + 工具调用
- [ ] M5 魔珐星云 3D 数字人 + TTS
- [ ] M6 100+ 轮长对话压测与记忆调优
- [ ] M7 参赛文档与演示视频

## 📄 许可

本项目基于 [Apache License 2.0](LICENSE) 开源（Copyright 2026 nobody5323）。
**声明：本项目借鉴 SillyTavern 架构思想，底层代码 100% 原创，不受 AGPL-3.0 协议传染。**
