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

## 验证与测试

```bash
npm run typecheck    # 渲染层 + 主进程
npm test             # 单测（几何 / 设置 / IPC 校验 / 静态服务 / 图标 / preload 边界）
npm run build        # 构建 + 发布物资源边界校验
npm run verify:release   # 连 release/ 一起校验（打包后）
```

---

## 本地数据

`%APPDATA%/HyPRA/`：

- `pet-window.json`、`pet-settings.json` —— 窗口位置与桌宠开关
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
