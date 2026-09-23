# 桌面桌宠端设计（desktop/）

> 目标：让 HyPRA 同时具备**两种形态** —— 浏览器里的 Web 端，与常驻桌面的**透明桌宠端**。
> 两者共用同一套后端（FastAPI）、同一套渲染器与 hooks，**只有一个前端源**。

---

## 1. 形态与分工

| | Web 端（`frontend/`） | 桌面端（`desktop/`） |
|---|---|---|
| 运行方式 | 浏览器访问 `localhost:3000` | Electron：**程序控制台**（常规窗口）+ **桌宠窗**（透明窗）+ 托盘常驻 |
| 界面 | 完整工作台（对话 / 记忆可见性 / 世界书 / 创作工坊 / 数字人设置） | 程序控制台（11 个分组，复用 Web 端面板）+ 桌面上的一只角色 + 气泡 + 一键聊天 |
| 数字人 | 魔珐 3D / Live2D / 立绘（三档降级） | **Live2D / 立绘**（不做 3D：桌宠需要透明小窗常驻，3D 那套留给 Web 端演示） |
| 配置入口 | 页面内的各类设置面板 | **程序控制台**（模型库 / 构图调试 / 插件 / 技能 / 预设 / 会话 / 创作工坊 / 桌宠设置 / 模式启动） |
| 后端 | 同一个 FastAPI（`localhost:8000`） | 同左 |

> **启动行为**：桌面端启动后先显示**程序控制台**（配置与调试主入口），
> **桌宠窗不再自动弹出** —— 由用户在控制台的「模式启动」里选「以桌面模式启动」（见 §9）。

换句话说：**桌面端不是 Web 端的复制品，而是"陪伴在场"的那一面**。评审看能力用 Web 端，日常陪伴用桌宠端。

---

## 2. 进程结构

```
desktop/
├── src/main/            主进程（CommonJS，tsc 编译到 dist/main）
│   ├── index.ts         组装：静态服务 → 托盘 → 设置 → IPC → 快捷键 → 显示窗口
│   ├── petWindow.ts     桌宠窗：透明/置顶/穿透两态/拖拽/位置持久化/显示器自愈
│   ├── consoleWindow.ts 控制台窗：常规窗口、关闭即隐藏、页面来源解析
│   ├── consoleIpc.ts    控制台 IPC（模式启动 / 桌宠设置 / 打开 Web 端）
│   ├── consoleSettings.ts 控制台设置（userData/console-settings.json，原子写）
│   ├── petGeometry.ts   窗口几何纯函数（可单测）
│   ├── petSettings.ts   设置读写（userData/pet-settings.json，原子写）
│   ├── petPositionStore.ts 位置读写（userData/pet-window.json，原子写）
│   ├── assetServer.ts   loopback 静态资源服务（含 CSP 响应头、路径逃逸防护）
│   ├── tray.ts          托盘菜单（显示/隐藏、穿透、置顶、缩放、退出）
│   ├── trayIcon.ts      运行时生成 PNG 图标（仓库里不放二进制资产）
│   ├── ipc.ts / ipcValidation.ts  IPC 注册与入参白名单校验
│   └── windowSecurity.ts 禁止导航 / 新窗 / webview
├── src/preload/pet.ts   contextBridge 白名单（桌宠窗唯一的桌面能力入口）
├── src/preload/console.ts 控制台的能力白名单（window.hyprConsole）
├── src/renderer/pet/    React 桌宠窗（Vite 独立入口，**自己写 UI**）
├── src/renderer/console/ React 程序控制台（Vite 第二入口，**复用 Web 端组件 + Tailwind**）
└── src/shared/ipc.ts    两端共享的类型与常量（禁止 import electron / react）
```

渲染层**复用 Web 端**（`@/` → `frontend/`）的部分：

- `lib/api/client.ts`：后端接口封装（对话、健康检查、TTS 探测）
- `lib/api/types.ts`、`lib/avatar/avatar-config.ts`、`lib/avatar/avatar-model-source.ts`
- `lib/avatar/avatar-renderer.ts`、`lib/avatar/static-portrait-renderer.ts`、`lib/avatar/portrait-assets`、`lib/live2d/*`
- `hooks/avatar/useAvatar.ts`（浏览器 TTS / 服务端 TTS 双实现 + 逐句降级）
- `hooks/chat/useChatSession.ts`（对话、记忆召回、情绪、世界书、工具调用）
- `hooks/avatar/useTtsPreferences.ts`
- `hooks/avatar/useAvatarModels.ts`（模型清单 / 上传 / 情绪映射 / 构图校准 / 当前选用）
- **`components/**`**（程序控制台直接复用 Web 端面板：模型库、能力中心、技能、LLM、人设与文风、会话、创作工坊）

桌面端**自己写**的部分：桌宠窗 UI（气泡、输入、右键菜单、设置面板）、
程序控制台的外壳与分组布局，以及全部窗口交互。
这样做的理由见 §3.3。

---

## 3. 六个关键决策（每条都对应一个真实故障）

### 3.1 页面由 loopback 静态服务提供，而不是 `file://`

前端资源引用全是**绝对路径**——`/live2d/<模型>/pet.model3.json`、`/portraits/happy.svg`、
`/vendor/cubism/Core/live2dcubismcore.min.js`（`frontend/lib/live2d/cubism-core.ts`）。
在 `file://` 下它们会解析成 `file:///live2d/...`（磁盘根目录）而全部 404。

因此主进程在 `127.0.0.1` 上起一个只读静态服务：

- 绝对路径与 Web 端**完全同源**，复用层一行都不用改；
- CSP 用**响应头**下发（不是 meta 标签），并能带上用户配置的后端源；
- 端口**固定优先**（34567，占用则递增）：渲染层的 `localStorage` 按 origin 隔离，
  端口一变，语音偏好 / 会话指针 / 凭证就全丢了。

安全边界：只绑回环、只允许 GET/HEAD、路径逐段校验拒绝 `..` 与盘符逃逸、
根目录之外的请求一律 404。

### 3.2 模型不入发布包，改从用户数据目录映射

发布物里**不得**出现受版权限制的角色模型与音频（项目 `AGENTS.md` 第 6 条）。
所以：

- 构建时只按白名单拷贝 `portraits/`（自研 SVG）与 `vendor/cubism/`（SDK，
  `scripts/setup-cubism.mjs` 落位才有），并显式删除 `live2d/`；
- `scripts/verify-release-assets.mjs` 在 `build` 与 `dist:win` 末尾扫描产物，
  发现模型 / 音频 / 密钥**直接让构建失败**；
- 用户自备的模型放到 `%APPDATA%/HyPRA/live2d/<模型id>/`，
  由静态服务把 `/live2d` 前缀映射过去（`scripts/install-live2d-model.mjs` 负责安装）。

### 3.3 桌宠窗是 Vite 独立入口，不是 Next 路由

如果桌宠窗复用 Next.js 页面，桌面端就必须自带一个 Next 服务进程才能启动——
"桌面端"变成"套壳浏览器 + 必须起两个服务"。改成 Vite 多入口后：

- 桌宠窗完全不依赖 Next，`npm run dev` / 打包后的 exe 都能独立跑；
- 复用仍然成立：渲染器与 hooks 通过 `@/` 别名直接引用（它们本来就是纯 React + 浏览器 API）；
- 代价是桌宠 UI 需要自己写一遍——但它本来就应该与工作台长得不一样
  （透明、无边框、只有角色和气泡），复用 `ChatPanel` 那种大面板反而不合适。

### 3.4 React 必须 dedupe

`frontend/` 有自己的 `node_modules`。不 dedupe 时，复用的文件会解析到**另一份 React**，
两个副本各有自己的 hooks dispatcher，症状是渲染层直接报
`Cannot read properties of null (reading 'useState')`。
`vite.config.mts` 的 `resolve.dedupe: ["react", "react-dom"]` 就是为了这个。

### 3.5 preload 只能 `require("electron")`

`preload` 跑在 sandbox 里，加载任何本地文件都会直接报错，
表现是 `window.hyprPet` **根本没被注入**（页面本身看起来正常）。
因此通道名在 preload 内联，类型安全由 `Record<PetChannelName, PetChannel>` 兜底
（shared 里增删通道、或把字符串写歪，tsc 都会报错）。
`tests/preload-imports.test.ts` 守住这条边界。

### 3.6 Windows 上的窗口状态会掉

`setAlwaysOnTop` 与 `setIgnoreMouseEvents` 在窗口 `show / focus / blur / restore`
之后会被系统重置，只在创建时设置一次不够，必须在这些事件里重新施加
（`applyWindowState()`）。点击穿透采用「穿透 + `forward: true`」两态：
穿透时鼠标事件仍转发给窗口，渲染层据此判断光标是否进入角色区域，
进入时才临时恢复可点击并显示「解锁交互」按钮。

拖拽则用 `event.screenX/screenY`（屏幕坐标）而不是 `clientX/clientY`：
拖拽期间窗口本身在移动，窗口内坐标会随之漂移；主进程还会以
`screen.getCursorScreenPoint()` 的真实光标为准，渲染层上报值只作兜底。

---

## 4. 与 Web 端的协作方式

- **无共享进程**：桌面端不代理任何业务请求，`/chat`、`/media/*` 都由渲染层直连后端。
- **后端地址**：构建期由 `HYPRA_API_BASE` 注入（默认 `http://localhost:8000`），
  同时用于 CSP 的 `connect-src` / `media-src`。后端换机器时重新构建即可。
- **CORS**：桌面端页面源是 `http://127.0.0.1:<端口>`。后端默认放行
  `CORS_ORIGIN_REGEX`（回环地址任意端口）——否则表现为界面一直「后端未连接」，
  而 curl 正常，极难定位（见 `backend/tests/test_app/test_cors.py`）。
- **降级链**：后端未起 → 桌宠显示「后端未连接」徽标，角色照常显示；
  服务端 TTS 不可用 → 自动回落浏览器原生语音；Cubism SDK 缺失 → 回落静态立绘。

---

## 5. 数据与隐私

桌面端**不接触任何模型 API Key**：对话与记忆全在后端进程，密钥只存在于 `backend/.env`。

`%APPDATA%/HyPRA/` 下只有：

- `pet-window.json`：桌宠窗位置
- `pet-settings.json`：缩放 / 置顶 / 穿透
- `live2d/`：用户自备的模型（由安装脚本放入）
- Electron 自身的缓存与 localStorage（沿用 Web 端的键名，因此语音偏好等保持一致）

---

## 6. 借鉴边界（合规）

桌宠的窗口机制参考了开源项目 `qiyueblues-design/zhuomianling`（MIT）的**做法**：
多入口 Vite、`base: "./"`、public 白名单拷贝、构建后资源审计脚本、
穿透两态与解锁入口、主进程轮询光标做视线跟随、拖拽后夹回工作区、
`pet-resource://` 式的用户资源隔离。

按项目约定（`AGENTS.md` 第 6 条），**只取机制思想，不复制代码与文案**：
本目录的 TypeScript 代码 100% 原创，注释与命名按本项目风格重写。
明确**不借鉴**的部分：它的 AI 调用与密钥管理放在 Electron 主进程
（我们已有 FastAPI 后端，把业务搬进主进程会与整体架构冲突）。

Live2D Cubism runtime 由官方 SDK 提供，使用与分发须遵守 Live2D 官方许可条款
（与本项目 Web 端同一约定，SDK 不入库、由 `scripts/setup-cubism.mjs` 落位）。

---

## 7. 验证方式

```bash
cd desktop
npm run typecheck     # 渲染层 + 主进程两套 tsconfig
npm test              # 68 项单测：几何 / 设置归一化（桌宠 + 控制台）/ IPC 校验 / 静态服务 / 图标 / preload 边界
npm run build         # 构建 + 资源边界校验
npm run dist:win      # 打包 portable + nsis，再校验安装包内容
```

人工验收（`npm run dev` 或打包后的 exe）：

0. 启动后先出现**「HyPRA 程序控制台」窗口**（11 个分组，样式与 Web 端一致）；
   托盘「程序控制台」可再次唤出；关闭窗口只是隐藏（不是退出）；
1. 「模式启动」→「以桌面模式启动」，桌面右下角出现**无边框、透明背景**的角色，可拖动，松手位置被记住；
2. 右键出菜单：开关穿透 / 置顶、缩放、打开对话、隐藏、退出；
3. 「穿透」后它不再遮挡其它窗口，鼠标移到角色上浮现「解锁交互」；
4. 托盘菜单与状态一致；`Ctrl+Alt+P` 显示 / 隐藏；
5. 打开对话说一句话 → 有回复、有情绪徽标、Live2D 表情跟着变；
6. 缩放 70%–150% 时窗口与内容同步（`--pet-scale` CSS 变量），不出现错位；
7. 「形象调试」里选模型、拖动预览 → 保存构图 → 桌宠窗里模型与底部按钮对齐（不再分离）；
8. 桌宠跑 Live2D 时**没有**米黄圆底座与情绪光晕（只留模型本身）；切到静态立绘时底座回来。

---

## 8. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 只有立绘、没有 Live2D | 未安装 Cubism SDK（`node scripts/setup-cubism.mjs <SDK目录>`）或模型未安装（`npm run install:model`） |
| 除静态立绘外什么都没有 | 静态服务被占用或杀软拦截；托盘 → 退出后重试，确认 34567 起可用 |
| 界面一直「后端未连接」 | 后端没起，或后端 CORS 未放行回环地址（见 §4） |
| 打包时卡在下载 | 需要 `ELECTRON_MIRROR`（Electron 二进制）与 `ELECTRON_BUILDER_BINARIES_MIRROR`（打包工具）；`electronDist` 已指向本地 Electron 以避免校验下载 |
| 安装包体积约 96MB | Electron 运行时固有体积；Web 端仍是轻量的浏览器访问 |

---

## 9. 程序控制台（`console.html`）

**定位**：桌面端的**配置与调试主入口**。启动后先看到它；桌宠窗与 Web 端都由用户在里面选。

### 9.1 为什么是独立窗口，而不是 Web 端的一个路由

需求原话是「Web 端和桌面的模型界面，让用户在程序控制台里选择调试好之后再选择哪个模式」——
即控制台必须是**程序自身**的一部分：

- Web 端路由要求 Next 服务先跑起来，而「选哪个模式启动」恰恰是**什么界面都还没启动时**要做的事；
- 桌面端不该为了改一个设置而依赖另一个前端服务。

因此控制台是**同一进程里的第二个 BrowserWindow**：常规窗口（有边框、可缩放、进任务栏），
关闭按钮 = **隐藏**（不是退出），由托盘「程序控制台」随时唤出。

### 9.2 复用方式：Tailwind + Web 端组件

控制台**不重写面板**。`frontend/components/**` 已核实**零 `next/*` 依赖**，可直接在 Vite 里渲染，
唯一缺口是样式：那些组件用 Tailwind 语义色（`bg-surface-panel` 等）。

- `desktop/postcss.config.mjs` + `desktop/tailwind.config.ts`（后者**从 `../frontend/tailwind.config` import 主题**，
  只覆盖 `content`）——色板单一来源，避免两端各写一份而漂移；
- `src/renderer/console/main.tsx` 引入 `@/app/globals.css`（`@tailwind` 指令 + 焦点环 + 页面底色）。

两个弹层组件（`LlmSettings` / `StudioPanel`）加了 `variant?: "overlay" | "inline"`：
Web 端仍是原来的弹层行为，控制台用 `inline`（不限尺寸、不抢焦点、不响应 Esc、不渲染关闭按钮）。

### 9.3 边界

| 事项 | 约定 |
|---|---|
| 打开 Web 端 | 只调 `shell.openExternal`，**不代起前端服务**（那会带来端口冲突与孤儿进程）；失败把原因返回界面 |
| URL 白名单 | `normalizeWebUrl` 只放行 http/https —— 这个值最终进 `openExternal`，而 `file://` / 自定义协议能借它拉起本机任意程序 |
| 桌宠设置 | 与桌宠窗共用同一个 `commitSettings`（校验 + 落盘 + 广播 + 刷新托盘），不存在两份状态 |
| 能力面 | `window.hyprConsole` 刻意**不含**拖拽 / 穿透 / 缩放这些桌宠专属能力（见 `src/preload/console.ts`） |
| 不代理业务 | 与桌宠窗一致：本进程不接触模型 API Key，控制台里的数据全部由后端提供 |

### 9.4 模型构图校准（解决「模型与底部按钮分离」）

桌宠窗里拖动只能移动**整个窗口**，改不了模型在窗口里的位置——分离的根因是**构图**：
不同 Live2D 模型的画布比例差别很大，而构图曾经是代码常量（`frontend/lib/live2d/model-assets.ts`）。

现在：

1. 模型元数据（`backend/data/avatar_models/<id>/model.json`）带 `layout: { scale, offsetX, offsetY, anchor }`；
2. 「形象调试」里**拖动预览即调位移**、滑块调缩放，点「保存构图」写入后端；
3. **Web 端与桌面端读同一份**（`GET /media/avatar/models`），所以调一次两边都对；
4. 「当前用哪个模型」也从 localStorage 迁到后端（`GET|PUT /media/avatar/models/selection`）——
   桌宠窗是 `127.0.0.1:34567`、Web 端是 `localhost:3000`，localStorage 天然不共享。

> 内置模型（`LIVE2D_MODELS.default`）的构图写在代码常量里，因此调试台里**能预览、不能保存**；
> 要保存校准，先在「模型库」上传模型并切换过去。

### 9.5 对话偏好（人设 / 文风 / 预设）也存后端

同一个问题的第二次出现：`useChatSession` 里的人设 / 文风 / 预设原本是**纯内存 state**，
初始值硬编码，既不持久化也不跨端——在控制台里选好，另两端打开还是老的。

现在三处（控制台 / Web 端 / 桌宠窗）读写同一份后端偏好：

| 端点 | 说明 |
|---|---|
| `GET /chat/preferences` | 四个字段：`persona_id` / `style_id` / `preset_id` / `st_preset_id`；**空串 = 未设置** |
| `PUT /chat/preferences` | 只改传进来的字段（不传 = 保持原值，空串 = 清除） |

优先级：**用户偏好 > 部署声明的默认**（`default_persona_id` / `default_style_id`）。
落盘 `backend/data/chat-preferences.json`（原子写）；文件被写坏时按「未设置」处理——
它只是「上次选了哪套提示词」，不该让对话起不来。

两条刻意的约束：

- **只有 `persona_id` 做存在性校验**：人设 id 同时是记忆隔离命名空间
  （`companion:{id}`），写错不只是这轮回复不对，而是记忆写进了另一个库；
  文风 / 预设写错顶多那一轮回落默认。
- **不就地改写正在运行的界面**：偏好是「下次打开时生效」——对话界面里的人设 / 文风
  是**会话级状态**（见 §1 的模式与作用域约定），从别处远程改它会让同一轮上下文语义漂移。
