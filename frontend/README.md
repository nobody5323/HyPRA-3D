# HyPRA 前端

情感陪伴对话 UI + 具身数字人（Next.js App Router + TypeScript + Tailwind）。

## 快速开始

```bash
cd frontend
npm install                       # 国内可加 --registry=https://registry.npmmirror.com
cp .env.local.example .env.local  # 按需修改 NEXT_PUBLIC_API_BASE
npm run dev                       # http://localhost:3000
```

> 需要先启动后端（`cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --reload`），
> 页面顶部会显示「后端在线 / 未连接」。

## 开发校验

```bash
npm run typecheck   # tsc --noEmit：与后端接口契约对齐
npm run lint        # ESLint（--max-warnings=0，当前 0 warning）
npm test            # vitest：接口契约 / 分段播报 / 退避重连
npm run build       # Next.js 生产构建（standalone）
```

测试只覆盖**容易安静出错、且靠手测难以复现**的三处（均有回归用例）：

| 文件 | 覆盖内容 |
|---|---|
| `tests/api.test.ts` | 后端用 `{}` 表示「无情绪/无播报」时的规范化、409 判重结构体、multipart 与 query 形状 |
| `tests/useAvatar.browser.test.ts` | 分段播报：逐段播完 / **被打断后不再出声** / 卸载后停止 / 整段抢占分段 |
| `tests/useAvatar.xmov.test.ts` | 断线退避重连：1s→2s→4s、**init 期间报错但随后连上不得重建**、重连用尽后降级；**语音事件消费**（SDK 回的 `"start"`/`"end"` 必须被识别，不得退化成靠超时收尾） |
| `tests/session-store.test.ts` | 本地会话指针与草稿：按陪伴对象分开、`null` = 新建对话、localStorage 不可用时静默降级 |

> 用例设计说明：分段播报的两条路径都用**可控替身**（`speechSynthesis` / `window.XmovAvatar`）
> 精确控制「一段念完」与「被取消」两种时序——真实环境里这两者都表现为「回到 idle」，
> 用状态判断就会错，因此必须用测试锁住。

## Docker 部署（评审一键，推荐）

前端已并入项目根目录的 `docker compose`（详见 [docs/deployment.md](../docs/deployment.md)）：

```bash
cd backend && cp .env.example .env      # 填自己的 LLM / Embedding key
cd .. && docker compose up -d --build   # 拉起 qdrant + backend + frontend
# 打开 http://localhost:3000
```

要点：

- `NEXT_PUBLIC_API_BASE` 是**构建期内联**变量，由 compose 的 `build.args` 传入
  `http://localhost:8000`（请求由宿主机浏览器发起，容器名 `backend` 无法解析）；
  改动后需 `docker compose build frontend` 重建，改 `.env` 重启无效。
- 镜像基于 Next.js **standalone** 产物（`next.config.mjs` 的 `output: "standalone"`），
  运行时镜像不含全量 `node_modules`；本地 `npm run dev` / `npm run build` 行为不变。
- `.env.local`（含魔珐密钥）已被 `frontend/.dockerignore` 排除，**不入镜像**。
- 前端等后端健康后再启动（compose 健康依赖链），首屏不会显示「后端未连接」。

## 已实现

### F1：对话产品化

| 能力 | 说明 |
|---|---|
| 对话 UI | 多轮对话、Enter 发送、打字中提示、错误提示 |
| **具身状态机** | 待机 / 聆听中 / 思考中 / 说话中（`hooks/avatar/useAvatar.ts`） |
| 字幕 | 消费后端 `speak.display_text`（已剥离 SSML 标签） |
| 情绪视觉 | 情绪标签 + 强度条 + 数字人光效随情绪变化 |
| 文风切换 | 清单来自后端 `GET /chat/styles`（预设与代码分离，新增 YAML 无需改前端） |
| Agent 工具提示 | 展示「✓ 已记录这次心情」等办事结果 |
| 播报 | 浏览器原生 TTS（零依赖，F1 占位实现） |
| 打断 | 客户端即时打断（中止请求 + 停止播报） |

### F2：魔珐具身驱动 SDK 集成

| 能力 | 说明 |
|---|---|
| **真实 3D 数字人** | 动态加载 `xmovAvatar.js`，SDK 挂载到 `#avatar-container` |
| **SSML 播报** | 把后端 `speak.ssml`（含 KA 动作）交给 `avatar.speak(ssml, true, true)` |
| 语音状态联动 | 监听 `onVoiceStateChange`（voice_start/voice_end）驱动状态机 |
| **自动降级** | 未配置密钥 / 脚本加载失败 / init 失败 → 回退**浏览器 TTS + 占位形象**，演示不中断 |
| 资源释放 | 卸载时调用 `avatar.destroy()`（释放 WebGL 资源） |

**配置方式**：在 `frontend/.env.local` 填入魔珐**驱动应用**密钥（留空即使用浏览器 TTS）：

```ini
NEXT_PUBLIC_XMOV_APP_ID=你的AppID
NEXT_PUBLIC_XMOV_APP_SECRET=你的AppSecret
```

> 页面右上角显示当前渲染方式（「魔珐 SDK」/「浏览器 TTS」）。
### F3：视觉与可访问性（浅色主题改造）

| 能力 | 说明 |
|---|---|
| **温和浅色主题** | 语义色板（`surface-*` / `ink-*` / `line` / `accent-*` / `success|danger|warning|brand` / `mood`）集中定义于 `tailwind.config.ts`；数字人画布保留深色（`stage.canvas`），形成「浅色卡片 + 深色画布」层次 |
| **对比度** | 正文 ≥ 4.5:1、装饰 ≥ 3:1（15 项颜色组合按 WCAG 公式实测）；`text-[11px]` 全部提升为 `text-xs` |
| **键盘与读屏** | 文风下拉为 listbox 语义（↑↓ / Escape / 点击外部关闭）、密钥面板为 dialog 语义（焦点进出 + Escape）、消息区 `role="log"` + `aria-live`、错误条 `role="alert"`、字幕/提示 `role="status"`、全站 `focus-visible` 焦点环 |
| **动效与触屏** | `prefers-reduced-motion` 支持、`touch-action: manipulation`、`overscroll-contain`、只用可插值属性做过渡 |
| **字体** | 中文优先**系统**字体栈（**零新增依赖**，未引入 webfont） |
| **模型预设** | 按模型分档的采样参数与推理开关（`GET /chat/presets`）；界面可选「自动（按模型匹配）/ 手动指定」 |
| **交互稳定性** | SDK 脚本加载超时降级、打断状态机回合隔离、两个 provider 的 `speak()` 语义对齐 |

## 待实现

- 多模态 Widget 展示（图片 / 字幕组件）
- 演示脚本逐幕对齐（见 [docs/frontend-plan.md](../docs/frontend-plan.md) 的 F7）

### F4：后端接口对接（个人记忆、人设、可见性、分段播报）

| 能力 | 说明 |
|---|---|
| **个人记忆（知识库）** | `KnowledgePanel`：上传文件 / 粘贴文本 → 解析清洗 → MinHash 判重（命中弹「覆盖 / 取消」）→ 分块入库；列表与删除（内联二次确认）。接口：`POST /knowledge/upload`、`GET /knowledge/list`、`DELETE /knowledge/{doc_id}` |
| **陪伴对象切换** | `PersonaSwitcher`：清单来自 `GET /chat/personas`；人设 id 即记忆命名空间（`companion_id`），切换时开新会话并清空界面记忆计数。仅 ≥2 项时渲染 |
| **记忆与行动可见性** | `MemoryTrace`：本轮命中世界书条目 / 个人记忆条数 / 情景记忆 / 语义事实 / 是否已后台写入 / 提示词 token；`AgentBadge`：MCP 服务器连接数、工具数、工具清单与错误 |
| **分段播报（流式分段）** | 对话区「分段播报」开关（**默认关闭**）：走 `POST /media/speak`（`streaming=true`）取 `chunks`（纯文本，字幕随段推进）与 `ssml_chunks`（逐段 SSML，由后端生成，前端不自拼标签），段间自动 `interactiveidle` 过渡 |
| **断线退避重连** | `avatar.onError` → 指数退避重建 SDK（1s → 2s → 4s，最多 3 次），期间 `stage = "reconnecting"`；重连用尽才降级为浏览器语音 |
| **契约层** | `lib/api/types.ts` 与后端响应一一对应；`lib/api/client.ts` 统一 `ApiError(status, detail)`（知识库 409 需读结构体），并把后端的空对象 `{}` 规范化为 `null` |
| **会话持久化** | 消息唯一来源是**后端**（会话落 SQLite）；前端只在 localStorage 记「当前会话 id」（`lib/chat/session-store.ts`），刷新后拉历史恢复。`SessionList` 提供历史列表 / 新建对话 / 打开历史会话；输入草稿也按角色本地保留 |

> 分段播报的段间停顿属于真机听感问题（SDK 状态切换经 WebSocket 下发），
> 因此默认关闭、由界面开关控制，不动已验证的整段播报主路径。

## 🔑 密钥配置（两种方式）

| 方式 | 操作 | 生效时机 | 适用 |
|---|---|---|---|
| **① 页面填写（推荐）** | 点击右上角**「配置数字人密钥」** → 填写 App ID / App Secret → 保存 | **即时生效**（SDK 自动重建） | 演示现场、临时切换 |
| ② 环境变量 | 在 `frontend/.env.local` 填 `NEXT_PUBLIC_XMOV_APP_ID/SECRET` | 需重新 `npm run build` | 部署时固化 |

- 优先级：**页面填写（localStorage）> 环境变量 > 降级浏览器语音**
- 页面填写的凭证**仅存本机浏览器**，不上传服务器；点「清除」即可移除
- 失败或未配置时**自动降级**为浏览器原生 TTS + 占位形象（对话/字幕/情绪不受影响）

## 目录

```
app/          页面（layout / page / globals.css）
components/   AvatarStage（数字人舞台）/ ChatPanel / SubtitleBar / MoodIndicator
              MemoryTrace（本轮记忆与行动）/ KnowledgePanel（个人记忆）/ AgentBadge（行动层）
              PersonaSwitcher / StyleSwitcher / PresetSwitcher / AvatarSettings / StateBadge
hooks/        useAvatar（具身状态机 + 播报 + 分段播报 + 退避重连）
              useChatSession（对话编排 + 人设切换）
              useAvatarCredentials（凭证来源）
lib/          api.ts（后端接口）/ types.ts（契约类型）/ persona.ts / avatar-config.ts
```
