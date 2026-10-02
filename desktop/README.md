# HyPRA 桌面桌宠端

桌面上的一只透明桌宠：Live2D / 立绘形象 + 气泡字幕 + 一键对话，常驻托盘。
对话、记忆、情绪、世界书、工具调用全部由后端（FastAPI）提供——
**本端不接触任何模型 API Key**。

> 设计说明与决策理由见 [`../docs/desktop-pet.md`](../docs/desktop-pet.md)。

---

## 前置条件

1. **后端已运行**（对话与语音都靠它）：

   ```bash
   # 项目根目录
   docker compose up -d --build          # 评审模式（含本地 Qdrant）
   # 或开发模式：backend 目录下
   ../.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
   ```

2. Node.js 18+（Windows 10/11）。

`npm install` 会自动补齐 Electron 二进制（国内网络先设镜像）：

```bash
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/   # cmd
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/" # PowerShell
npm install
```

---

## 开发运行

```bash
cd desktop
npm run dev
```

会同时起 Vite（渲染层，HMR）、`tsc --watch`（主进程）与 Electron。

**桌面端与 Web 端可以同时开**：Web 端在 3000（Next.js），桌宠窗在 Vite 5173 / 生产静态服务 34567，互不干扰。

---

## 形象来源

| 想要的效果 | 做法 |
|---|---|
| Live2D 模型 | ① `node scripts/setup-cubism.mjs <CubismSdkForWeb 目录>`（在 `frontend/` 下执行）落位 SDK；② `npm run install:model` 把模型装到 `%APPDATA%/HyPRA/live2d/default/` |
| 只用静态立绘 | 什么都不用做（`frontend/public/portraits/*.svg` 已随包分发） |

`npm run install:model -- --from D:/my-models --id myModel` 可指定来源与模型 id；
`npm run install:model -- --list` 查看已安装的模型。

模型**不入库也不进安装包**（授权原因）：安装包只带自研立绘与 SDK 运行时的白名单资源，
构建末尾的 `verify:assets` 会扫描产物并在发现模型 / 音频 / 密钥时让构建失败。

---

## 操作方式

| 操作 | 说明 |
|---|---|
| 左键拖动角色 | 移动桌宠（松手后位置被记住） |
| 右键 | 菜单：打开对话 / 桌宠设置 / 点击穿透 / 窗口置顶 / 放大 / 缩小 / 隐藏 / 退出 |
| 底部「聊天」 | 展开对话面板（消息、输入框、打断） |
| 底部「穿透」 | 开启后桌宠不再遮挡其它窗口；鼠标移到角色上会浮现「解锁交互」 |
| 底部「隐藏」 | 收进托盘（`Ctrl+Alt+P` 或托盘左键唤出） |
| 托盘 | 显示/隐藏、穿透、置顶、缩放、退出 |
| `Esc` | 关闭菜单 / 设置面板 |

缩放范围 70%–150%（步进 5%），窗口与内容同步缩放。

---

## 打包 Windows 安装包

```bash
cd desktop
npm run dist:win
```

产物在 `release/`：

- `HyPRA-<版本>-portable.exe` —— 免安装，双击即用
- `HyPRA-Setup-<版本>.exe` —— NSIS 安装包（可选安装目录、创建快捷方式）

**产物是自包含的**：`resources/` 下已内置 `backend/backend.exe`（PyInstaller 产物，自带 Python 运行时）
与 `qdrant/qdrant.exe`。目标机器**不需要装 Python / Node / Qdrant**，双击即用；未压缩约 590 MB，
NSIS 压缩后 250–350 MB。

打包前需要两样捆绑资源就位（`npm run verify:bundled` 会把关，缺失时打印可照抄的补救命令）：

| 资源 | 来源 |
|---|---|
| `backend/dist/backend/` | `cd backend && pyinstaller backend.spec --noconfirm` |
| `_local/qdrant/qdrant.exe` | 自备二进制（`_local/` 已被 gitignore），版本需与 `backend/pyproject.toml` 的 `qdrant-client` 对齐 |

打包依赖两个二进制下载，国内网络建议先设镜像：

```bash
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
```

> 说明：`package.json` 里的 `build.electronDist` 指向本地已安装的 Electron，
> 避免打包时再去 GitHub 拉包校验（那一步在国内网络下会超时）。

---

## 后端地址

默认连 `http://localhost:8000`。后端跑在别的机器 / 别的端口时，构建期指定：

```bash
set HYPRA_API_BASE=http://192.168.1.20:8000
npm run build        # 开发模式则重启 npm run dev
```

该地址同时用于 CSP 的 `connect-src` / `media-src`，因此改地址后必须重新构建渲染层。

---

## 本机服务（Qdrant + 后端）

**桌面端代管这两个进程**：启动时按设置拉起，关掉 HyPRA（包括被强制结束后的下次启动）时一起收掉。

| 服务 | 源码模式的启动方式 | 就绪判据 |
|---|---|---|
| Qdrant | `_local/qdrant/qdrant.exe`（v1.19.1，与 `backend/pyproject.toml` 的 `qdrant-client` 对齐） | `127.0.0.1:6333/healthz` |
| 后端 | `.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000` | `127.0.0.1:8000/health` |

安装包形态下这两条换成内置的 `resources/backend/backend.exe` 与 `resources/qdrant/qdrant.exe`
（主进程按 `app.isPackaged` 自动切换，不需要手工配置）。

在控制台「总览 → 本机服务」里可以：看每个服务的状态与 PID、单独启停、
开关「自动启动」与「后端 `--reload`」、以及指定项目根 / 解释器 / Qdrant 路径。

几条刻意的行为：

- **端口上已经有服务在跑时不会被接管**：如果你自己开了一个 Qdrant，控制台会显示「外部运行中」，
  既不重复启动也不去停它（不会抢端口、不会误杀）；
- **后端带 `--reload` 会产生两级子进程**，收尾更依赖进程树清理，所以默认不开；
- **两种形态的路径来源不同**：源码模式从项目根找 `.venv` 与 `_local/qdrant`；安装包形态从自身
  `resources/` 读内置的 `backend.exe` 与 `qdrant.exe`，数据则落在 `%APPDATA%/HyPRA/`；
- 日志在 `%APPDATA%/HyPRA/logs/`，启动超时会把日志路径一并写在失败原因里。

---

## 验证与测试

```bash
npm run typecheck    # 渲染层 + 主进程
npm test             # 单测（几何 / 设置 / IPC 校验 / 静态服务 / 图标 / preload 边界 / 本机服务）
npm run build        # 构建 + 发布物资源边界校验
npm run verify:bundled   # 打包前置检查（backend.exe / qdrant.exe 等捆绑资源是否就位）
npm run verify:release   # 连 release/ 一起校验（打包后）
```

---

## 本地数据

`%APPDATA%/HyPRA/`：

- `pet-window.json`、`pet-settings.json` —— 窗口位置与桌宠开关
- `console-settings.json`、`service-settings.json` —— 控制台偏好与本机服务设置（含路径覆盖）
- `.env` —— 后端配置（安装包首次启动从模板生成，**填 LLM / Embedding key 的地方**）
- `data/` —— 后端可写数据（SQLite 记忆库 / 会话 / 用户内容 / 插件状态）
- `qdrant/` —— 向量库 storage（安装包形态；源码形态在 `_local/qdrant/`）
- `logs/` —— 受管服务的 stdout / stderr（`qdrant.log` / `backend.log`）与 `services.json`
- `live2d/` —— 你安装的模型
- Electron 自身的 `Local Storage` / 缓存

删除该目录即可恢复出厂状态（模型需重新安装）。

---

## 排障

| 现象 | 处理 |
|---|---|
| 双击 exe 没反应 | 检查是否已有实例在跑（单实例锁，第二个实例会唤出已有窗口） |
| 一直显示「后端未连接」 | 后端没起；或后端 CORS 未放行回环地址（`CORS_ORIGIN_REGEX`） |
| 只有立绘没有 Live2D | SDK 或模型未安装，见上文「形象来源」 |
| 快捷键无效 | `Ctrl+Alt+P` 可能被其它程序占用（启动日志会有提示），用托盘代替 |
| 重新 `npm install` 后 Electron 起不来 | npm 11 默认不执行依赖的安装脚本；本项目的 `postinstall` 会自动补齐，若失败手动执行 `node node_modules/electron/install.js` |
| 控制台里服务显示「启动失败」 | 源码模式：检查 `.venv` 与 `_local/qdrant` 是否存在，或在「本机服务」里指定路径；安装包模式：`resources/backend` 与 `resources/qdrant` 应随包分发，缺失说明安装不完整，重装即可 |
| 首次启动后只有占位回复 | 安装包首次启动会生成 `%APPDATA%/HyPRA/.env`，其中 `LLM_PROVIDER=mock` 只回占位文本；填入真实 key 并重启服务即可 |
| 服务显示「外部运行中」却停不掉 | 那是你自己（或上次残留）启的进程，桌面端刻意不去杀别人的进程；需要用任务管理器 / `taskkill` 手动收 |
