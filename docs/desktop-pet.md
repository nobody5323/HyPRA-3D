# 桌面桌宠端设计（desktop/）

> 目标：让 HyPRA 同时具备**两种形态** —— 浏览器里的 Web 端，与常驻桌面的**透明桌宠端**。
> 两者共用同一套后端（FastAPI）、同一套渲染器与 hooks，**只有一个前端源**。

---

## 1. 形态与分工

| | Web 端（`frontend/`） | 桌面端（`desktop/`） |
|---|---|---|
| 运行方式 | 浏览器访问 `localhost:3000` | Electron：**程序控制台**（常规窗口）+ **桌宠窗**（透明窗）+ 托盘常驻 |
| 界面 | 完整工作台（对话 / 记忆可见性 / 世界书 / 创作工坊 / 数字人设置） | 程序控制台（11 个分组，复用 Web 端面板）+ 桌面上的一只角色 + 气泡 + 一键聊天 |
| 数字人 | 魔珐 3D / Live2D / 立绘（三档降级） | 同左，**三档都在**（3D 用竖屏应用，见 §3.7） |
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
- `hooks/avatar/useAvatar.ts`（服务端 TTS / 魔珐 SDK / 静默三实现 + 逐句降级）
- `hooks/chat/useChatSession.ts`（对话、记忆召回、情绪、世界书、工具调用）
- `hooks/avatar/useTtsPreferences.ts`
- `hooks/avatar/useAvatarModels.ts`（模型清单 / 上传 / 情绪映射 / 构图校准 / 当前选用）
- **`components/**`**（程序控制台直接复用 Web 端面板：模型库、能力中心、技能、LLM、人设与文风、会话、创作工坊）

桌面端**自己写**的部分：桌宠窗 UI（气泡、输入、右键菜单、设置面板）、
程序控制台的外壳与分组布局，以及全部窗口交互。
这样做的理由见 §3.3。

---

## 3. 七个关键决策（每条都对应一个真实故障）

### 3.1 页面由 loopback 静态服务提供，而不是 `file://`

前端资源引用全是**绝对路径**——`/live2d/<模型>/pet.model3.json`、`/portraits/happy.svg`、
`/vendor/cubism/Core/live2dcubismcore.min.js`（`frontend/lib/live2d/cubism-core.ts`）。
在 `file://` 下它们会解析成 `file:///live2d/...`（磁盘根目录）而全部 404。

因此主进程在 `127.0.0.1` 上起一个只读静态服务：

- 绝对路径与 Web 端**完全同源**，复用层一行都不用改；
- CSP **两处下发**：响应头（本服务，能带上用户配置的后端源）+ 页面里的 `<meta>`
  （`pet.html`）。浏览器同时收到时取**交集**，所以放行一个外部来源必须两处都改
  ——只改一处会被另一处静默挡掉（`tests/pet-csp.test.ts` 钉住了这一点）。
  具体要放行哪些（含 `worker-src blob:` 与形象数据 CDN）见 §3.7；
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

**可交互区域必须覆盖所有该点的东西**（角色本体 + 底部状态区 + 打开的浮层）。
右键菜单绑在**整个窗口**上（`.petStage` 的 `onContextMenu`），而菜单与设置面板都会
画到角色区域之外——鼠标一移上去就触发 `pointerleave`、穿透重新打开，浮层立刻点不动。
症状是「右键弹出菜单后点什么都没用」，且**只在穿透模式下复现**（`clickThrough: true`）。
修法：浮层打开期间强制保持可交互，关闭时按 `:hover` 恢复；判定用 `:hover`
而不是自己记一个状态——元素之间移动会先 `leave` 再 `enter`，
自己记会在中间那一帧误判成「离开了」，把点击吃掉。

**窗口尺寸必须按理论值重算，不能沿用 `getBounds()` 读回的值。**
在 **150% 缩放**的屏幕上，Electron 的窗口尺寸会在**每次移动后 +1 DIP**：
当年请求 380×480（那时的基准尺寸），读回就已经是 382×482，之后每动一次再 +1。
原先 `movePetWindowDrag` 写的是 `width: bounds.width`（把读回值原样写回去），
于是**每次拖动都变大一点**——而 `scale` 恒为 1.0，设置面板一直显示「100%」，
用户只能看到桌宠在无端膨胀（实测拖 12 次量到 392×492）。
修法：尺寸一律由 `expectedPetSize()` 按 `PET_BASE_SIZE × scale` 重算
（拖动中 / 拖动结束 / 显示器变化三条路径都改），实测 12 次移动后稳定在请求的尺寸。
单测覆盖不到真实窗口行为，退一步在 `tests/pet-window-size.test.ts` 里把
**写法**钉住（`setBounds` 的参数里不许出现 `bounds.width`）；
手工验证工具是 `_local/win-probe-drag.cjs`（不入库）。

**窗口宽度要贴着角色，右键菜单要按实测尺寸落位**（两条一起改，因为互相牵制）。

- **宽度**：基准尺寸从 380×480 收到 **280×480**。角色本体只有 200×200、
  魔珐竖屏容器约 209×372，380 宽的窗口左右各空 80+px——那片透明区看不见，
  却照样吃鼠标事件（非穿透模式下挡住下层窗口的点击），右键也会在离角色很远的地方弹菜单。
  **高度不动**：角色区高 372 是「窗口高 − 上下留白」算出来的（顶部 56px 给字幕气泡、
  底部 52px 给状态条），压矮窗口就是压矮角色。
- **菜单**：`ContextMenu` 原先用写死的 `MENU_WIDTH = 188` / `ITEM_HEIGHT = 26` 夹取位置，
  既没乘 `--pet-scale` 也没算分隔线，而菜单真实高度是 321px（它以为 306px）。
  后果：105% 缩放下贴底右键，**最后一项「退出 HyPRA」被窗口裁掉**（下溢 28px、右溢 14px），
  因为菜单只能画在窗口内，而 `html, body` 是 `overflow: hidden`。
  修法：落位规则抽成纯函数 `renderer/pet/pet-menu.ts`（单测见 `tests/pet-menu.test.ts`），
  尺寸由 `getBoundingClientRect()` **实测**（缩放、字号、hint 行数、分隔线全都自动包含），
  规则与系统原生菜单一致——顺光标右下展开 → 放不下就翻到另一侧 → 两侧都放不下才夹回窗口，
  窗口矮于菜单时（最后一道保险）改为菜单内部滚动，绝不顶出窗口。
  首帧先 `visibility: hidden` 量完再显示，`useLayoutEffect` 保证在绘制前完成，不会闪。

### 3.7 桌宠也用魔珐 3D：透明通道 + 竖屏容器

原先的理由是「桌宠需要透明小窗常驻，3D 那套留给 Web 端演示」——但实测下来这个理由
**不成立**。用一次性探针（`_local/xmov-probe/`，不入库）验了两件事：

1. **数字人自带透明通道**。魔珐 SDK 是**浏览器端本地渲染**（WebGL + canvas），叠在容器里，
   容器底色会从人物四周透出来。Web 端当初垫深色底只是「没验证过」的保守做法
   （`AvatarStage.tsx` 里写着「SDK 是否自绘不透明背景无法从代码判定」）。
   所以它与 `transparent: true` 的桌宠窗天然契合，**不需要去背**。
2. **一个 appId 不能用于两种比例**。横竖屏是**魔珐控制台创建应用时**选定的，
   容器比例必须与应用类型一致，否则人物被拉伸 / 跑偏。所以桌宠用**竖屏 9:16** 容器
   （`aspect-ratio: 9 / 16`，高度撑满内容区），凭证按形态分开存：
   `web`（横屏，Web 端）/ `pet`（竖屏，桌宠窗），见 `backend/app/api/media.py`。

连带的三个必改点：

- **CSP 有两处**（§3.1）：`pet.html` 的 `<meta>` 与 `assetServer` 的响应头，
  缺一处就会被另一处挡掉。而且需要放行**四种**东西，每一种都是被 SDK 报错逼出来的
  （都不是文档给的）：
  1. `media.xingyun3d.com` —— SDK 脚本（唯一放行的外部脚本，**保持精确**）；
  2. `worker-src 'self' blob:` —— SDK 从 `blob:` 创建 Web Worker。
     **缺这条时它只报「浏览器能力检查失败」**，而 WebGL / 编解码器其实全部正常，
     极易误判成 GPU 或 UA 问题而走错方向；
  3. `*.xingyun3d.com` / `wss://*.xingyun3d.com` —— 会话 API 与**驱动 WebSocket**，
     后者实际在 `ttsa-gateway-lite.xingyun3d.com`（**官方接入文档里没有这个域名**）；
  4. `public-xmov.oss-cn-hangzhou.aliyuncs.com` —— 形象数据（`char_data.bin`），
     而且是 **fetch** 拉的，只写进 `img-src` 不管用，必须进 `connect-src`。

  连接类用**厂商级通配**而不是逐个列：逐个试错的代价是每轮「改 CSP → 重建 → 重启 →
  复现」，而通配的边界仍限定在魔珐自己的域名空间（`script-src` 没跟着放宽）。
  `_local/xmov-probe/electron-diagnose.cjs` 能把每一层拦截原样打出来（一次性，不入库）——
  它用**与桌宠窗相同的 webPreferences** 开一个隐藏窗加载 `pet.html`，
  并把渲染层的 console 与加载失败事件原样输出。
- **热区**：3D 生效时 `.petBody` 从「200×200 的圆」扩成整个内容区——竖屏容器高约 372px，
  圆装不下，而热区小于角色会出现「鼠标在人物身上，解锁按钮却不出来」。
  代价是容器内的透明区也算热区（**已知取舍**：比「整窗都算热区」精确，
  又不必按像素抠出人形轮廓）。
- **层级必须显式声明，并把 SDK 隔离在层叠上下文里**：魔珐 SDK 会给它创建的
  canvas 设 `z-index: 100`（从它自己的源码里 grep 出来的，还不止一处，
  另有 `99999` / `1000`），而 `pet.css` 原先**一个 `z-index` 都没有**、全靠 DOM 顺序
  ——于是右键菜单 / 聊天面板 / 设置面板全被模型盖住（**点得到但看不见**，
  因为穿透与热区不受层级影响）。
  修法：`.petStage` / `.petBody` / `.petXmov` 各设非 `auto` 的 `z-index` 成为
  **层叠上下文**，把 SDK 的 `100` 锁死在角色层内部；浮层则按
  「气泡 < 底部状态条 < 面板 < 菜单」明确分层（`--pet-z-*` 变量集中定义）。
  **教训**：引入第三方渲染 SDK 时，「谁盖住谁」不能依赖 DOM 顺序。
- **等凭证期间不要挂本地渲染器**：首屏 `credentials` 还是 `null`（要向后端拉），
  `auto` 因此被判成「本地渲染」，Live2D 立刻开始加载（注入 Cubism Core、下载模型）；
  等凭证到达再切到 3D 时，Cubism Core 的 `<script>` 注入后**不会移除**、模型也已下载
  ——白加载一趟（用户反馈「桌面是不是自动加载 live2d」就是这个）。
  所以 `auto` / `xmov` 下要等 `loading` 结束再决定挂不挂本地渲染层；
  用户明确选了 `local` / `static` 时不依赖凭证，可以立即挂。

形象选择规则（`auto` / `xmov` 走魔珐，`local` / `static` 强制本地）与 Web 端**刻意一致**，
抽成纯函数 `src/renderer/pet/avatar-source.ts` 并由单测钉住。

---

## 4. 与 Web 端的协作方式

- **无共享进程**：桌面端不代理任何业务请求，`/chat`、`/media/*` 都由渲染层直连后端。
- **后端地址**：构建期由 `HYPRA_API_BASE` 注入（默认 `http://localhost:8000`），
  同时用于 CSP 的 `connect-src` / `media-src`。后端换机器时重新构建即可。
- **CORS**：桌面端页面源是 `http://127.0.0.1:<端口>`。后端默认放行
  `CORS_ORIGIN_REGEX`（回环地址任意端口）——否则表现为界面一直「后端未连接」，
  而 curl 正常，极难定位（见 `backend/tests/test_app/test_cors.py`）。
- **降级链**：后端未起 → 桌宠显示「后端未连接」徽标，角色照常显示；
  魔珐 SDK 建不起来 / 重连用尽 → 回落本地渲染（Live2D → 立绘），并把原因显示在设置面板里；
  服务端 TTS 不可用 → 自动静默（只显示文字）；Cubism SDK 缺失 → 回落静态立绘。
- **形象选择**：`auto` / `xmov` 走魔珐（需凭证且未降级），`local` / `static` **强制本地**
  ——与 Web 端同一套规则（`src/renderer/pet/avatar-source.ts`，有单测钉住）。

---

## 5. 数据与隐私

桌面端**主进程不接触任何模型 API Key**：对话与记忆全在后端进程；
部署默认密钥只存在于 `backend/.env`。

一处必须说清的例外（客户端渲染所必需）：魔珐 SDK 在**渲染层**建会话，因此它的
`appId` / `appSecret` 会由后端（`GET /media/avatar/credentials`）下发到页面
——这**不是「只存服务器」**。用户填写的凭证落在 `backend/data/avatar-credentials.json`
（按形态分 `web` / `pet` 两套，`data/` 已 gitignore），防护依赖后端 CORS 只放行回环地址
（**不要**把 `CORS_ORIGINS` 设成 `"*"`）。

`%APPDATA%/HyPRA/` 下只有：

- `pet-window.json`：桌宠窗位置
- `pet-settings.json`：缩放 / 置顶 / 穿透
- `live2d/`：用户自备的模型（由安装脚本放入）
- Electron 自身的缓存与 localStorage（沿用 Web 端的键名，因此语音偏好等保持一致）

### 5.1 行踪数据（唯一的「历史」）

后端会在**同一个目录**下写 `perception/`（`activity-YYYY-MM-DD.jsonl` +
`activity-current.json`）——它是「主人最近在电脑上做什么」的记录，
供角色在合适的时机主动搭话（「忙完了？」「回来啦」）。

设计见 `docs/proactive-multimodal.md` §4.9，四条与桌面端直接相关的约束：

| 约束 | 落地 |
| --- | --- |
| **默认只记程序名** | 窗口标题需要显式开 `perception_activity_include_title`。程序名是**类别**（`Code.exe`），标题是**内容**（可能是一份体检报告） |
| **保留期有限** | 默认 7 天，按天分文件、过期即删 |
| **必须可见** | 桌宠设置面板里逐段列出「它到底记了什么」——用户只能靠它说的话去猜，而猜出来的结论一定比实际更糟 |
| **一键清干净** | 面板上的「清除行踪记录」走 `POST /perception/activity/clear`；在能力中心关掉开关时也会**立刻**清（不等下一次上报——用户很可能「先关开关，再关掉桌宠窗」） |

采集仍然遵守 §4 的进程边界：主进程只采集原始字段，行踪的**记录与聚合全在后端**，
渲染层只是把字段报上去。

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
npm test              # 86 项单测：几何 / 设置归一化（桌宠 + 控制台）/ IPC 校验 / 静态服务 / 两处 CSP 一致性 / 形象来源决策 / 图标 / preload 边界
npm run build         # 构建 + 资源边界校验
npm run dist:win      # 打包 portable + nsis，再校验安装包内容
```

人工验收（`npm run dev` 或打包后的 exe）：

0. 启动后先出现**「HyPRA 程序控制台」窗口**（11 个分组，样式与 Web 端一致）；
   托盘「程序控制台」可再次唤出；关闭窗口只是隐藏（不是退出）；
1. 「模式启动」→「以桌面模式启动」，桌面右下角出现**无边框、透明背景**的角色，可拖动，松手位置被记住；
2. 右键出菜单：开关穿透 / 置顶、缩放、打开对话、隐藏、退出；
   在窗口**右下角**右键（最坏位置）菜单也完整可见，最后一项「退出 HyPRA」不被裁；
3. 「穿透」后它不再遮挡其它窗口，鼠标移到角色上浮现「解锁交互」；
4. 托盘菜单与状态一致；`Ctrl+Alt+P` 显示 / 隐藏；
5. 打开对话说一句话 → 有回复、有情绪徽标、Live2D 表情跟着变；
6. 缩放 70%–150% 时窗口与内容同步（`--pet-scale` CSS 变量），不出现错位；
7. 「形象调试」里选模型、拖动预览 → 保存构图 → 桌宠窗里模型与底部按钮对齐（不再分离）；
8. 桌宠跑 Live2D 时**没有**米黄圆底座与情绪光晕（只留模型本身）；切到静态立绘时底座回来；
9. 「桌宠设置」→ 形象选「魔珐星云 SDK」、填入**竖屏**应用的密钥 → 保存后角色变成 3D
   （透明背景，能看到它后面的桌面）；故意填错密钥 → 回落到 Live2D，且面板里显示降级原因。

---

## 8. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 只有立绘、没有 Live2D | 未安装 Cubism SDK（`node scripts/setup-cubism.mjs <SDK目录>`）或模型未安装（`npm run install:model`） |
| 3D 选了却不生效、回落 Live2D | 没填密钥（桌宠要**竖屏**应用，与 Web 端的横屏凭证分开存）；或 SDK 被 CSP / 网络拦掉——「桌宠设置」里会显示降级原因 |
| 3D 人物被拉伸 / 跑偏 | 容器比例（9:16）与控制台创建应用时选的应用类型不一致：横屏应用配竖屏容器就会这样，需要另建一个竖屏应用 |
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
| 打开 Web 端 | 只调 `shell.openExternal`，**不代起 Next 前端服务**（Web 端是另一种部署形态，要跑前端构建产物）；打不开就把原因返回界面。**本机 Qdrant 与后端由控制台代管**，见 §9.6 |
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

### 9.6 代管本机服务（Qdrant + 后端 + Web 前端）

**定位**：控制台是启动中心，所以「启动」不应该只是打开一个窗口——本机的三个进程也归它管。

> 本节修订了§9.3 原来「桌面端不代你启动服务」的结论：那一条的本意是**不自动拉 Next 前端**
> （另一种部署形态），不是不碰本机依赖。
>
> 后来这一条也放宽了：前端**仍不自动启动**，但已纳入受管清单——用户点「以 Web 模式启动」时
> 由桌面端先把它拉起来，而不是开一个打不开的浏览器页。见下方「但 Web 前端不在自动启动之列」。

| 服务 | 启动方式 | 就绪判据 |
|---|---|---|
| `qdrant` | `_local/qdrant/qdrant.exe`，cwd 落在它所在目录（storage 跟着走） | `GET 127.0.0.1:6333/healthz` |
| `backend` | `.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`，cwd 在 `backend/` | `GET 127.0.0.1:8000/health` |
| `frontend` | `npm run dev -- --port 3000`（Windows 上过一层 `cmd.exe /c`：`npm` 是 `npm.cmd`，`CreateProcess` 不能直接执行它），cwd 在 `frontend/` | `GET 127.0.0.1:3000/` |

端口显式钉住 `--port 3000`：默认端口被占时 Next dev 会自己往后找（3001、3002…），
而健康检查只探 3000——不钉的话那种情况会表现成「进程活着但一直不就绪」，比直接失败难查得多。

启动顺序是**依赖顺序**：Qdrant 就绪后才起后端；Qdrant 失败则后端直接标记为失败
（后端启动时要连向量库，硬起只会留下一个半死的进程）。

**但 Web 前端不在自动启动之列**——`startAll` 只拉 Qdrant 与后端。前端属于另一种部署形态，
桌宠根本用不到它，程序一开就拉一个 Next dev 服务是白搭（内存与端口都白占）。它由两条路径
按需拉起：控制台「本机服务 → 启动」，以及「模式启动 → 以 Web 模式启动」。
后者的实现见 `consoleIpc.ts` 的 `openWeb`：**地址指向本机前端默认端口时**，先把服务拉到
就绪再开浏览器（否则用户点开的是「无法访问此网站」）；指向远程地址或别的端口则不管——
那种前端不归这台机器管。起不来就**不开浏览器**，直接把原因返回给界面。

也因此 `SERVICE_IDS`（受管清单，决定状态列表与退出收尾）与 `startAll`（随程序自动拉起的那些）
不是一回事：前者三个都在，后者只有两个。

**四个必须处理好的点**（`src/main/serviceManager.ts`）：

| 坑 | 做法 |
|---|---|
| **端口已被占用** | 启动前先探一次健康检查：已有同类服务在跑就标为 `adopted`（外部运行中），**既不重复拉起、也绝不停止它**——否则就成了抢端口、误杀用户自己开的 Qdrant |
| **孤儿进程** | 停止时用 `taskkill /PID <pid> /T /F` 杀整个进程树（Windows 上 `child.kill()` 杀不掉 `uvicorn --reload` 的孙进程）；启动时把 PID 落盘，下次启动据此清理上次被强杀留下的孤儿，**但先核对进程映像名**（PID 会被系统回收，不能照着旧记录盲杀） |
| **异步退出** | `before-quit` 不支持 `await`：先 `preventDefault()` 按住，收完再 `app.exit(0)`；整体再加 8s 上限，卡住也要退得出去 |
| **不碰 API Key** | 起子进程只给 `cwd`，**不读不传不记任何环境变量**（硬约束见 §4）；模型密钥一律由后端自己读 `backend/.env` |

**路径解析**：分两种形态（判据是 `app.isPackaged`，而不是猜路径）：

| 形态 | 后端可执行文件 | Qdrant 可执行文件 | 数据落点 |
|---|---|---|---|
| 源码（`npm run dev`） | `<项目根>/.venv/Scripts/python.exe` | `<项目根>/_local/qdrant/qdrant.exe` | `<项目根>/backend/data`、`_local/qdrant/storage` |
| 安装包 | `resources/backend/backend.exe` | `resources/qdrant/qdrant.exe` | `%APPDATA%/HyPRA/` |

源码形态还要在 `__dirname`、`app.getAppPath()`、exe 所在目录、`process.cwd()` 这几处逐级向上找
「同时含 `.venv` 与 `_local/qdrant` 的目录」（两样都在才算，只看 `.venv` 会误判别的 Python 项目）；
找不到就用服务设置里的路径覆盖。

Web 前端目录同样按形态推导（源码 `<项目根>/frontend`，安装包 `resources/frontend`），
但它的**前置检查与后端完全分开**（`describeMissingFrontendPath`）：前端要的是项目目录本身与它
自己装好的 `node_modules`，与 Python、Qdrant 一概无关——拿后端那套判据去查它，报出来的错会指错方向。
`node_modules` 单独查是因为 `npm run dev` 在依赖缺失时**能起进程但立刻退出**，
报出来的是一句难懂的模块解析错误；先拦住才能直接说清「先 cd frontend 再 npm install」。

打包形态下前端目录本来就指不到东西（`electron-builder` 的 `extraResources` 只带 backend 与 qdrant），
这时直接报「发布包不含 Web 端」，而不是让人去改一个不存在的目录——**这是刻意的边界，不是漏配**。

**状态面**：`ready` / `adopted` / `failed` / `starting` / `stopped` 五态，日志落
`%APPDATA%/HyPRA/logs/<服务>.log`（启动超时会直接把日志路径写在失败原因里）。

### 9.7 自包含打包（安装包形态）

**目标**：目标机器**不装 Python / Node / Qdrant**，双击即用。

`resources/` 的布局（`package.json` 的 `build.extraResources`）：

```
resources/
  app.asar                          ← Electron 渲染层（控制台 + 桌宠窗）
  backend/backend.exe + _internal/  ← PyInstaller onedir 产物（自带 Python 运行时，~136 MB）
  backend-res/.env.example + THIRD_PARTY_LICENSES/
  qdrant/qdrant.exe                 ← ~82 MB
```

未压缩约 590 MB；NSIS 压缩后 250–350 MB。

**后端侧的两条分界线**（`backend/app/paths.py`）：

| | 冻结形态取哪 | 开发形态取哪 |
|---|---|---|
| **只读资源**（yaml 预设 / 世界书 / 技能 / 插件 / MCP 清单） | `sys._MEIPASS`（打在 exe 内） | `backend/` |
| **可写数据**（SQLite / 用户内容 / 插件状态 / 媒体产物） | `%APPDATA%/HyPRA/` | `backend/` |

打包后 `__file__` 会指向 PyInstaller 的临时解包目录、cwd 也不再是 `backend/`——所以
**全仓不再有模块级 `__file__` 路径推导**（只剩 `paths.py` 一处），而改造前是 11 处散落。
`data_root()` 在冻结形态下刻意与 Electron 的 `app.getPath("userData")` 取同一个目录，
于是两边自动指向同一处，**不需要注入环境变量**。

**MCP 的自举**：stdio 传输要用一个可执行文件拉起 server 进程，而冻结形态下
`sys.executable` 就是 `backend.exe`——它不认识 `.py` 参数，会被当成 uvicorn 启动，
造成**端口冲突与无限自我重启**。所以 `${PYTHON}` 在冻结形态下被改写成
`backend.exe --mcp-server <script>`，由 `run_backend.py` 用 `runpy` 在进程内执行
（`mcp_servers.json` 一行都不用改，开发形态不受影响）。

**合规**：Qdrant 是 **Apache-2.0**（不是 AGPL），可再分发，但必须随包附许可证——
故有 `backend/THIRD_PARTY_LICENSES/`，随 `extraResources` 一起分发。
`verify-release-assets.mjs` 对 `.pem` 的封禁为两处公开 CA 证书 bundle
（`certifi/cacert.pem`、`grpc/.../roots.pem`）开了**路径白名单**：它们是 TLS 根证书，
不含私钥，却是 Python / grpc 运行时的必需品。

**打包前置检查**：`npm run verify:bundled`（已挂在 `pack` / `dist:win` 之前）确认
`backend/dist/backend/`、`_local/qdrant/qdrant.exe`、`.env.example`、`THIRD_PARTY_LICENSES` 都在。
前两样**不在仓库里**（一个是 PyInstaller 产物，一个是自备二进制），新克隆的人缺了只会看到
electron-builder 一句「cannot find …」，所以脚本会直接打印可照抄的补救命令。
