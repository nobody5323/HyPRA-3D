# HyPRA-3D：打通提示词架构与混合记忆的情感陪伴 3D 交互系统

[![CI](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml/badge.svg)](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)
[![Tests](https://img.shields.io/badge/tests-CI%20verified-brightgreen)](https://github.com/nobody5323/HyPRA-3D-/actions/workflows/ci.yml)

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
- **可插拔语音（TTS）与音画同步**：声音与渲染解耦——魔珐 SDK 走它自带 TTS；
  Live2D / 静态立绘可接**自部署 GPT-SoVITS**（零样本音色克隆、多音色可切）；
  两者都不可用时**保持静默**（只显示文字，不用系统语音顶替）。服务端 TTS 路径下，音频与口型时间轴
  **来自同一次请求**（音画同源），字幕按音频播放进度推进；任一句合成失败只降级那一句，
  对话不中断。切换入口在前端「数字人设置 → 语音引擎」。
- **酒馆（SillyTavern）预设兼容**：可直接导入你在酒馆里用的 **Chat Completion 预设 JSON**，按它的
  条目顺序、启用开关、注入深度（含 In-Chat `depth`）、`use_sysprompt` 覆盖、扩展注入槽与
  `{{char}}` / `{{getvar::}}` 等宏组装提示词；前端提供**参数面板 + 条目编辑器**
  （拖拽/↑↓ 排序、逐条开关、展开改正文/depth/role、记忆注入落点、导出回酒馆），
  并显式列出「该预设哪些部分在本项目不生效」而不静默失败。
  契约、组装语义与合规边界见 [`docs/st-preset-compat.md`](docs/st-preset-compat.md)。
- **用户自定义角色与世界书（创作工坊）**：在界面里创建自己的陪伴角色（角色名 / 一句话定位 /
  简介 / 标签 / **人设正文** / **背景故事**）与**按条件触发的世界设定**（关键词 / 正则 /
  语义向量三通道，归属可选「所有角色」或某个专属角色），并带 **「试触发」**——输入一句用户
  可能会说的话，逐通道显示会不会命中、为什么（命中了哪些关键词 / 哪个正则 / 语义相似度与阈值）；
  内容写完后**下次对话即生效**（无需重启服务）。内置角色与条目不可直接修改（可复制，也可从当前工坊删除；包内文件不改），
  唯一例外是内置条目的**启用开关**——它写用户侧偏好，内置文件一字未改。
  数据契约、接口与不变量见 [`docs/user-content-studio.md`](docs/user-content-studio.md)。
- **两种形态：Web 端 + 桌面桌宠端**：同一套后端与渲染层，两种「在场」方式——
  浏览器里是完整工作台（对话 / 记忆可见性 / 世界书 / 创作工坊 / 魔珐 3D 数字人），
  桌面上是一只**透明无边框桌宠**（Live2D / 立绘 + 气泡字幕 + 一键对话，托盘常驻，
  支持点击穿透、置顶、70%–150% 缩放、全局快捷键）。桌宠端不接触任何 API Key，
  对话与记忆仍走后端；设计说明与决策理由见 [`docs/desktop-pet.md`](docs/desktop-pet.md)。

## 🧠 设计参照与文档

完整文档地图见 [`docs/README.md`](docs/README.md)。核心几篇按主题列出：

**架构与能力**

| 文档 | 内容 |
|---|---|
| [`docs/memory-architecture.md`](docs/memory-architecture.md) | **混合记忆引擎**（四层记忆 / 统一混合检索 / 生命周期 / 参数总表） |
| [`docs/prompt-composition.md`](docs/prompt-composition.md) | **提示词逐节构成**（真实渲染样例、预算与裁剪、两条组装路径） |
| [`docs/proactive-multimodal.md`](docs/proactive-multimodal.md) | **多模态感知与主动沟通**（ASR / 视觉 / 桌面情景 / 时间感 / 行踪画像 + 主动链路与七道节制闸门） |
| [`docs/st-preset-compat.md`](docs/st-preset-compat.md) | **酒馆预设兼容契约**（字段映射 / 组装语义 / 宏 / 不支持清单 / 合规边界） |
| [`docs/preset-ai-adaptation.md`](docs/preset-ai-adaptation.md) | **预设 AI 适配**（导入社区预设后一键改造为陪伴形态） |
| [`docs/jailbreak-layer.md`](docs/jailbreak-layer.md) | **叙事框架层**（默认关闭的框架声明层：三段结构、注入位置与边界） |
| [`docs/user-content-studio.md`](docs/user-content-studio.md) | **创作工坊**（用户自定义角色 / 背景故事 / 世界书 / 文风的数据契约与不变量） |

**前端与形态**

| 文档 | 内容 |
|---|---|
| [`docs/frontend-plan.md`](docs/frontend-plan.md) | 前端方案（Next.js + 魔珐 SDK + 具身状态机） |
| [`docs/frontend-avatar-integration.md`](docs/frontend-avatar-integration.md) | 前端 SDK 接入指南（含 speak 注意事项、外部 TTS 路径与 FAQ） |
| [`docs/desktop-pet.md`](docs/desktop-pet.md) | **桌面桌宠端**（形态分工、进程结构、关键决策与合规边界） |

**插件体系**

| 文档 | 内容 |
|---|---|
| [`docs/plugin-development.md`](docs/plugin-development.md) | **插件开发指南**（manifest / 能力面 / 权限 / 声明式配置，公开接口说明） |
| [`docs/plugin-market.md`](docs/plugin-market.md) | 插件市场设计（索引 / 安装 / 回滚 / 安全边界；**设计稿，未实现**） |

**参赛与合规**

| 文档 | 内容 |
|---|---|
| [`docs/competition-gap-analysis.md`](docs/competition-gap-analysis.md) | **赛题差距分析**（评审维度对照与行动优先级） |
| [`docs/why-embodied-avatar.md`](docs/why-embodied-avatar.md) | **不可替代性论证**（为何情感陪伴需要具身数字人） |
| [`docs/sillytavern-memory-design-reference.md`](docs/sillytavern-memory-design-reference.md) | 记忆与提示词机制的设计参照调研 |
| [`docs/license-compliance.md`](docs/license-compliance.md) | **许可与合规说明**（AGPL 义务 / §13 检查清单 / 来源纪律 / 依赖审计） |
| [`docs/deployment.md`](docs/deployment.md) | 部署说明（Docker Compose 一键部署 / 开发模式） |
| [`AGENTS.md`](AGENTS.md) | 项目开发约定（架构分层、红线、验证要求） |

## 🚀 快速开始

### 一键启动（Windows）

根目录双击 `start.bat`，或在命令行执行 `start.bat [参数]`——脚本会做环境自检、
`.env` 兜底提醒、端口占用检测、后端就绪等待；若 `.env` 用本机 Qdrant，会先拉起
`_local/qdrant/qdrant.exe`。每端各占一个独立窗口，日志分开可见，**关闭窗口即停止该端**。

    start.bat                    # 后端 + 桌面端（默认：先开程序控制台）
    start.bat web                # 后端 + Web 前端（改 Web 端的最短路径）
    start.bat all                # 后端 + Web 前端 + 桌面端
    start.bat backend            # 只启动后端
    start.bat frontend           # 只启动 Web 前端
    start.bat console            # 只启动桌面端（后端已在别处跑着）
    start.bat docker             # docker compose 一键部署（评审模式）
    start.bat check              # 只做环境自检，不启动服务

    start.bat desktop -Release   # 桌面端用构建产物启动（演示 / 评审用）

默认目标（后端 + 桌面端）的用意：**先开程序控制台**——模型库、构图调试、插件、技能、
预设、会话、创作工坊都在里面，桌宠窗与 Web 端再由它的「模式启动」里选定；
桌宠窗与 Web 端**不会自动弹出**。

> 依赖（`.venv` / `node_modules`）缺失时只提示修复命令，不擅自安装。
> 端口已有实例则跳过并复用；桌面端对自己没启的后端是「接管显示」，不会重复拉起、
> 也不会把它停掉。

### 手动分步启动

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
# 未填 / 初始化失败 → 自动降级为本地渲染 + 静默，对话、字幕、情绪均不受影响
# 想给 Live2D / 静态立绘配「真声音」→ 在 .env 配 DIGITAL_HUMAN_PROVIDER=gpt_sovits
# （需另起 GPT-SoVITS 服务；未部署时自动降级，无需改任何配置），见 docs/deployment.md
```

评审模式（docker compose 一键部署，见 [`docs/deployment.md`](docs/deployment.md)）。

```bash
# 桌面桌宠端（第三种入口，可选；需后端已运行）
cd desktop
npm install
npm run dev             # 桌面右下角出现透明桌宠，托盘常驻
# 打包安装包（自包含：内置后端与 Qdrant，目标机无需 Python / Node）
# npm run dist:win → release/ 下的 portable exe 与 NSIS 安装包
# 形象：未装 Cubism SDK 时用自研立绘；装好后 npm run install:model 启用 Live2D
# 详见 desktop/README.md 与 docs/desktop-pet.md
```

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
    desktop/     Electron 桌宠端（透明桌宠窗 + 托盘；复用 frontend 的渲染器与 hooks）
    docs/        设计文档与部署说明（参赛说明 / 演示脚本待产出）
    _local/      本机私有资料（自备 SDK/模型包、开发留档、旧备份）——不入库
    AGENTS.md    项目开发约定
    LICENSE

## 📄 合规声明

> **本项目以 GNU AGPL-3.0 发布。** 借鉴 SillyTavern 的提示词工程与记忆体系**思想**，
> 以及其数据格式规范（角色卡 CCv2/CCv3、世界书、预设 JSON）；
> 除文件中明确标注来源的第三方组件外，本项目代码为原创实现。
> 项目内测试语料 / 世界书 / RAG 语料仅使用自创或公有领域内容。
>
> **关于语音与音色克隆（GPT-SoVITS）**：本项目只提供接入能力，不自带任何音色模型与参考音频。
> 使用他人的声音做音色克隆可能**侵犯声音权 / 人格权**，请仅使用自己录制的声音、
> 或已获得明确授权的素材。

**关于「酒馆预设兼容」的合规边界**（详见 [`docs/st-preset-compat.md`](docs/st-preset-compat.md) §1）：

| 类别 | 处理 |
|---|---|
| **发行内容**（仓库源码 / 内置预设 / 镜像 / 文档） | **零** SillyTavern 或社区预设的提示词原文；内置预设全部自写 |
| **运行时用户数据**（用户自己导入的预设 JSON） | 存本机 `backend/data/presets/`（已 gitignore），只解析与渲染，**不入库、不随发行物分发** |

实现上另有两道防护：导入时**剥离且不保存**端点/密钥类字段（避免误存用户凭证）；
导入的预设文件**只读**，界面编辑写入独立的覆盖层文件（可随时「恢复导入时」）。

**关于「创作工坊（用户自定义角色 / 世界书）」的合规边界**
（详见 [`docs/user-content-studio.md`](docs/user-content-studio.md) §5）：

| 类别 | 处理 |
|---|---|
| **发行内容**（仓库源码 / 内置角色与世界书 / 文档） | 全部自写，无第三方提示词原文 |
| **用户自撰内容**（角色卡 / 背景故事 / 世界书条目） | 只落本机 `backend/data/studio/`（已 gitignore），**不入库、不随发行物分发** |
| 用户对内置内容的偏好（如停用某条内置条目） | 写在用户侧的 `disabled_builtin.json`，发行物永不被运行期行为污染 |

## 📌 路线图

- [x] M1 提示词架构（分层 System Prompt + 世界书三通道触发）
- [x] M2 混合记忆引擎（热 / 温 / 冷三层）
- [x] M3 RAG + LangGraph 推理编排
- [x] M4 情绪识别 + 工具调用（function calling + MCP）
- [x] M5 数字人驱动**指令层**（SSML 播报 + KA 动作 + 渲染无关时间轴）
- [x] M6 前端 + 魔珐 SDK 接入（对话 UI / 流式字幕 / 具身状态机 / 降级与退避重连）
- [x] M6.5 前端对接全部后端接口（个人记忆上传、人设与文风清单、行动层状态、分段播报）
- [x] M6.7 酒馆（SillyTavern）预设兼容（导入 / 组装语义 / 宏 / 前端参数面板与条目编辑器）
- [x] M6.8 用户自定义角色与世界书（创作工坊：角色卡 / 背景故事 / 条目归属 / 试触发）
- [ ] M7 100+ 轮长对话压测与记忆调优
- [ ] M8 参赛文档与演示视频

> M6/M6.5 完成的是**代码与接口对齐**：真实渲染效果依赖魔珐密钥与积分
> （页面「数字人设置」或 `frontend/.env.local` 填写后即可验证，未填则不播报语音）。
> 缺口清单与优先级见 [`docs/competition-gap-analysis.md`](docs/competition-gap-analysis.md)。

## 📄 许可

本项目基于 [GNU Affero General Public License v3.0](LICENSE) 开源（Copyright 2026 nobody5323）。

**声明**：本项目借鉴 SillyTavern 的提示词工程与记忆体系**思想**及其数据格式规范
（角色卡 CCv2/CCv3、世界书、预设 JSON）；除文件中明确标注来源的第三方组件外，
代码为原创实现。项目采用与本生态一致的 AGPL-3.0，**不适用于闭源二次分发**。

**AGPL §13 义务**：本项目以网络服务形式提供时，必须让用户能获取对应版本的源码
（自带 Web 界面须暴露可点击的源码入口）。实现方式与检查清单见
[`docs/license-compliance.md`](docs/license-compliance.md)。
