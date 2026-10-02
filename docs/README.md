# docs/ 文档地图

> 本目录是 HyPRA 的设计与说明文档集合。本页是**唯一索引**，新增文档请同步登记在此。
>
> 阅读约定：**权威**（契约/不变量以此为准）· **说明**（解释现状与取舍）· **参考**（外部调研，非本项目实现）· **快照**（阶段性状态，会随进展更新）。

---

## 建议阅读顺序

1. **先看全局**：根目录 [`README.md`](../README.md)（功能亮点 + 快速开始）→ [`AGENTS.md`](../AGENTS.md)（架构分层与红线）
2. **理解核心机制**：[`prompt-composition.md`](prompt-composition.md)（提示词逐节构成）→ [`memory-architecture.md`](memory-architecture.md)（四层记忆）
3. **按需深入**：酒馆兼容 / 创作工坊 / 感知与主动 / 插件 / 前端与桌宠 / 部署
4. **参赛与合规**：[`competition-gap-analysis.md`](competition-gap-analysis.md) → [`why-embodied-avatar.md`](why-embodied-avatar.md) → [`license-compliance.md`](license-compliance.md)

---

## 架构与能力

| 文档 | 定位 | 内容 |
|---|---|---|
| [`prompt-composition.md`](prompt-composition.md) | 说明（**契约级**） | 每轮对话实际写进提示词里的内容；逐节构成、预算与裁剪、两条组装路径、真实渲染样例 |
| [`memory-architecture.md`](memory-architecture.md) | 说明（**契约级**） | 四层记忆（工作 / 情景 / 语义 / 个人）、统一混合检索（BM25 + 向量 + RRF）、生命周期、参数总表 |
| [`proactive-multimodal.md`](proactive-multimodal.md) | 说明（**契约级**） | 感知链路（ASR / 视觉 / 桌面情景 / 时间感 / 行踪画像）+ 主动链路（触发器 → 七道节制闸门 → 复用对话图 → SSE 推送）与隐私红线 |
| [`st-preset-compat.md`](st-preset-compat.md) | 说明（**契约级**） | 酒馆（SillyTavern）预设兼容契约：字段映射、组装语义、宏、不支持清单、合规边界 |
| [`preset-ai-adaptation.md`](preset-ai-adaptation.md) | 说明 | 导入社区预设后的「一键 AI 适配」（规则层 + 规划层，改造为陪伴对话形态） |
| [`jailbreak-layer.md`](jailbreak-layer.md) | 说明 | 叙事框架层（术语对齐 ST 的 `jailbreak` 槽位；默认关闭，三段结构与 `<boundary>` 边界） |
| [`user-content-studio.md`](user-content-studio.md) | **权威** | 创作工坊：用户自定义角色 / 背景故事 / 世界书 / 文风的数据契约、接口与不变量 |

## 前端与形态

| 文档 | 定位 | 内容 |
|---|---|---|
| [`frontend-plan.md`](frontend-plan.md) | 说明 | 前端方案：技术栈、具身状态机、目录结构、接口契约、分期计划 |
| [`frontend-avatar-integration.md`](frontend-avatar-integration.md) | 说明 | 魔珐星云 SDK 接入指南：初始化、speak 注意事项、外部 TTS 路径、音色与声随情变、FAQ |
| [`desktop-pet.md`](desktop-pet.md) | 说明 | 桌面桌宠端：形态分工、进程结构、七个关键决策、程序控制台、自包含打包 |

## 插件体系

| 文档 | 定位 | 内容 |
|---|---|---|
| [`plugin-development.md`](plugin-development.md) | **权威**（插件公开接口） | 插件开发指南：manifest 字段、`build(ctx)` 契约、能力面、权限声明、声明式配置 |
| [`plugin-market.md`](plugin-market.md) | 说明（**设计稿，未实现**） | 插件市场设计：索引 / 安装 / 升级回滚 / 安全边界 / 信赖边界 / 合规 |
| [`examples/plugin-hello/`](examples/plugin-hello/) | 示例 | 可运行的最小插件（tool + settings，只读不联网） |

## 参赛与合规

| 文档 | 定位 | 内容 |
|---|---|---|
| [`competition-gap-analysis.md`](competition-gap-analysis.md) | 快照（内部对齐） | 赛题评审维度对照、我方现状与缺口、行动优先级 |
| [`why-embodied-avatar.md`](why-embodied-avatar.md) | 说明（论证） | 不可替代性论证：为何情感陪伴需要具身数字人（参赛核心章节底稿） |
| [`license-compliance.md`](license-compliance.md) | **权威** | 许可与合规：AGPL 三项义务与 §13 检查清单、代码来源纪律、插件许可边界、依赖审计 |
| [`deployment.md`](deployment.md) | 说明 | 部署说明：docker compose 一键部署（评审模式）与开发模式、配置键位速查 |

## 参考（外部调研，非本项目实现）

| 文档 | 定位 | 内容 |
|---|---|---|
| [`sillytavern-memory-design-reference.md`](sillytavern-memory-design-reference.md) | 参考 | SillyTavern 记忆与提示词机制调研 → HyPRA 设计参照（只借鉴思想，不复制代码） |

---

## 约定

- **单一事实来源**：涉及接口 / 数据契约 / 不变量的内容，以标注「权威」的文档为准；其余文档如与之冲突，应改说明文档。
- **进度类状态**（✅/⬜、里程碑、P1–P5 等）易过期：修改实现时请顺手更新对应文档的状态表；本目录中的「快照」类文档尤其需要定期复核。
- **合规**：任何文档都不得包含第三方提示词原文、他人角色卡正文或本地勘察数据（见 [`license-compliance.md`](license-compliance.md) 与 `AGENTS.md` §6）。
