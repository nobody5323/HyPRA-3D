# 许可与合规说明（License & Compliance）

> 本文件是 `AGENTS.md §6` 的落地细则。协议迁移或新增第三方代码时必须同步更新本文件。

## 1. 项目协议

| 项 | 值 |
| --- | --- |
| 协议 | **GNU Affero General Public License v3.0** |
| 全文 | [`LICENSE`](../LICENSE)（标准 AGPL-3.0 文本，未修改） |
| SPDX 标识 | `AGPL-3.0-only`（`backend/pyproject.toml`、`frontend/package.json`、`desktop/package.json`） |
| 版权 | Copyright 2026 nobody5323 |

**为什么选 `-only` 而不是 `-or-later`**：上游生态（SillyTavern、TauriTavern、tellev）均声明为
`AGPL-3.0`（即 v3）。本项目如果是 `-or-later`，就无法合法吸收仅以 v3 授权的代码；
选择 `-only` 保留了对上游代码的最大兼容空间。

## 2. AGPL-3.0 的三项实际义务

1. **分发即开源**：分发修改版时必须提供完整对应源码。
2. **§13 网络服务条款**：以网络服务形式向用户提供时，必须让用户能获取**对应运行版本的源码**。
   —— 这是 AGPL 区别于 GPL 的核心条款，也是本项目（Web 应用）**最容易漏掉**的一条。
3. **许可证与版权声明保留**：不得移除他人的版权声明与许可声明。

### 2.1 §13 的落地要求（检查清单）

- [ ] Web 界面提供**可点击的源码入口**（页脚「源代码」链接，指向本项目仓库对应 tag/commit）
- [ ] 该入口在**未登录状态**下也可访问（§13 面向所有网络用户）
- [ ] 链接指向的版本与**部署版本一致**（建议通过构建期注入 commit hash 实现）
- [ ] 桌面端（Electron）在「关于」中同样展示源码地址与协议
- [ ] 若将来提供托管服务，须在服务说明中明示源码获取方式

> 说明：SillyTavern 上游本身并未在界面中提供源码链接；本项目作为参赛项目按**严格合规**执行，
> 以便在评审中作为「开源规范」的加分项。

## 3. 代码来源纪律

复用任何 AGPL 来源的代码（SillyTavern / TauriTavern / tellev / 社区扩展）时，必须：

1. 在**文件头**标注：来源仓库、文件路径、提交 hash；
2. **保留**原版权声明与许可头；
3. 登记到下方「第三方借鉴清单」。

**未登记的复用视为违规**（`AGENTS.md §6` 硬约束）。

### 3.1 第三方借鉴清单

| 来源 | 提交/版本 | 借用内容 | 文件 | 登记日期 |
| --- | --- | --- | --- | --- |
| SillyTavern | 1.18.0 | **仅机制思想与数据格式规范**（未复用代码） | — | — |
| — | — | *（尚无代码级复用）* | — | — |

> 当前项目**尚无任何代码级复用**：与 SillyTavern 的关系是「思想借鉴 + 数据格式兼容」。
> 一旦引入代码复用，必须在此表登记。

## 4. 数据格式兼容 vs 内容版权

| 类型 | 性质 | 使用限制 |
| --- | --- | --- |
| 角色卡格式（CCv2 / CCv3）、世界书字段、预设 JSON 结构 | 社区公开规范 | 实现兼容**不受限** |
| 他人角色卡的**正文**、世界书**条目内容**、提示词原文 | 他人作品 | 受其版权约束：不可入库、不可作演示素材、不可分发 |
| 本地酒馆实例的勘察数据 | 他人作品（且可能含成年人内容） | 一律只读、不入库、不截图、不入测试夹具（见 `AGENTS.md §8.6`） |
| 用户自行导入的数据 | 用户行为 | 允许；但**不得随产品分发** |

规范出处：`character-card-spec-v2`（malfoyslastname）、`character-card-spec-v3`（kwaroran）。

## 5. 插件许可边界

插件与本项目的耦合方式决定其许可要求：

| 耦合方式 | 许可认定 | 插件作者可用的协议 |
| --- | --- | --- |
| 进程内 import（Python）、同一 JS 上下文 | 视为衍生作品 | 必须 AGPL 兼容协议 |
| **进程外 / 隔离边界**（子进程、MCP、iframe、Web Worker） | 视为独立程序 | 作者自选（MIT / Apache / AGPL 均可） |

**因此插件宿主必须**：

- 在插件管理 UI 中**明示**该边界（"此插件与宿主同进程，须以 AGPL 兼容协议授权"）；
- 优先提供**隔离式加载路径**，以保全第三方插件生态的协议自由度；
- 安装第三方插件时展示其**来源、协议与 hash**，由用户确认。

## 6. 依赖许可审计

新增依赖入库前检查其协议；发布前生成依赖报告备查。

| 范围 | 命令 | 状态 |
| --- | --- | --- |
| 后端（Python） | `pip-licenses --format=markdown`（需安装） | 待接入 |
| 前端 / 桌面（npm） | `npx license-checker --summary` | 待接入 |
| 整体 | 发布前人工复核 + 结果归档到 `docs/` | 待接入 |

> 参照 tellev 的 `dependencyReport` Gradle 任务：把依赖报告作为**发布物的一部分**产出，
> 便于评审核验。本项目计划以 npm script + Python 脚本各出一个报告。

### 6.1 随包分发的第三方二进制

桌面端发布包（`desktop/package.json` 的 `extraResources`）里包含两个**独立程序**。
它们各自的许可证与 HyPRA 的 AGPL 无关，但再分发时必须履行对应义务：

| 组件 | 版本 | 许可证 | 义务 | 许可证落位 |
| --- | --- | --- | --- | --- |
| Qdrant | 1.19.1 | **Apache-2.0** | 保留许可证与版权声明（§4(a)(c)） | `backend/THIRD_PARTY_LICENSES/qdrant-LICENSE.txt`，随包拷到 `resources/backend-res/THIRD_PARTY_LICENSES/` |
| PyInstaller | 6.22.3 | GPL-2.0-or-later **+ bootloader 例外** | bootloader 的例外条款明确允许打包非 GPL 程序；自建 spec 属构建产物 | 无需随附（构建工具），升级时复核 |

> **边界判断**：Qdrant 以独立进程运行，PyInstaller 只是构建工具——两者都不与
> HyPRA 代码形成衍生关系，因此既不会「传染」AGPL，也不受 AGPL 传染。
>
> 升级 Qdrant 版本时必须重新核对其 LICENSE（见 `backend/THIRD_PARTY_LICENSES/README.md`）。

> 另：打包产物的资源边界由 `desktop/scripts/verify-release-assets.mjs` 硬校验。
> 它禁止模型/音频/密钥类文件；`certifi/cacert.pem` 与 `grpc/.../roots.pem`
> 是**公开 CA 证书 bundle**（非密钥），已按精确路径白名单放行，否则后端无法发起 HTTPS。

## 7. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-23 | 协议由 Apache-2.0 迁移至 AGPL-3.0；建立本文件 |
| 2026-09-25 | 阶段二自包含打包：登记随包分发的 Qdrant（Apache-2.0）与 PyInstaller 运行时义务；新增 `backend/THIRD_PARTY_LICENSES/` |
