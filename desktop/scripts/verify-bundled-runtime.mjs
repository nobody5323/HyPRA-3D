/**
 * 发布前的捆绑运行时守卫：确认 extraResources 的源都在。
 *
 * 为什么需要这个脚本：安装包要内置 `backend.exe` 与 `qdrant.exe`，而这两样
 * 都不在仓库里：
 *   - `backend/dist/backend/`      ← 由 PyInstaller 产出（见 backend/backend.spec）
 *   - `_local/qdrant/qdrant.exe`   ← 用户自备二进制（`_local/` 已被 gitignore）
 *
 * 新克隆仓库的人直接跑 `npm run dist:win`，只会看到 electron-builder 一句
 * 「cannot find …」，不知道该装什么。这里提前把关，缺哪项就打印可直接
 * 照抄的补救命令。
 */
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoRoot = path.dirname(desktopRoot);

/** 每项：标签 + 必需的源路径 + 缺失时的补救命令 */
const REQUIREMENTS = [
  {
    label: "后端打包产物",
    target: path.join(repoRoot, "backend", "dist", "backend", "backend.exe"),
    fix: [
      "cd backend",
      "..\\.venv\\Scripts\\python.exe -m pip install pyinstaller",
      "#   若 pip 源报 403（清华源偶发失效）：加 -i https://mirrors.aliyun.com/pypi/simple/",
      "..\\.venv\\Scripts\\python.exe -m PyInstaller backend.spec --noconfirm",
    ],
  },
  {
    label: "Qdrant 二进制",
    target: path.join(repoRoot, "_local", "qdrant", "qdrant.exe"),
    fix: [
      "下载 Windows 版并解压到 _local/qdrant/（须与 backend/pyproject.toml 的 qdrant-client 对齐）：",
      "  https://github.com/qdrant/qdrant/releases/download/v1.19.1/qdrant-x86_64-pc-windows-msvc.zip",
    ],
  },
  {
    label: "环境变量模板",
    target: path.join(repoRoot, "backend", ".env.example"),
    fix: ["随仓库分发；缺失说明工作区不完整：git checkout backend/.env.example"],
  },
  {
    label: "第三方许可证",
    target: path.join(repoRoot, "backend", "THIRD_PARTY_LICENSES"),
    fix: ["随仓库分发；缺失说明工作区不完整：git checkout backend/THIRD_PARTY_LICENSES"],
  },
];

const missing = REQUIREMENTS.filter((item) => !existsSync(item.target));

if (missing.length > 0) {
  console.error("✗ 打包前置检查未通过：以下捆绑资源缺失\n");

  for (const item of missing) {
    console.error(`  · ${item.label}：${path.relative(repoRoot, item.target)}`);

    for (const line of item.fix) {
      console.error(`      ${line}`);
    }

    console.error("");
  }

  console.error("补齐后重新执行本命令。");
  process.exit(1);
}

console.log(`✓ 打包前置检查通过（${REQUIREMENTS.length} 项捆绑资源齐备）`);
