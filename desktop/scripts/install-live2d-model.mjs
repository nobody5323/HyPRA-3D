/**
 * 安装 Live2D 模型到桌面端的数据目录。
 *
 * 为什么不随发布包分发模型：模型授权由使用者自行确认（与项目 .gitignore 的约定一致），
 * 因此桌面端把 `/live2d` 映射到**用户数据目录**，由本脚本负责把模型放进去。
 *
 * 用法：
 *   node scripts/install-live2d-model.mjs                      # 从 ../frontend/public/live2d 拷 default
 *   node scripts/install-live2d-model.mjs --from D:/models --id myModel
 *   node scripts/install-live2d-model.mjs --list               # 看看装了什么
 */
import fs from "node:fs/promises";
import fsSync from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** 与主进程的 app.setName("HyPRA") 保持一致（userData 目录名由它决定） */
const APP_DATA_DIR_NAME = "HyPRA";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const defaultSource = path.resolve(desktopRoot, "../frontend/public/live2d");

function readArgument(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);

  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

/** 用户数据目录（Windows：%APPDATA%/HyPRA；其它平台按 Electron 规则近似） */
function userDataDirectory() {
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming"), APP_DATA_DIR_NAME);
  }

  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", APP_DATA_DIR_NAME);
  }

  return path.join(process.env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config"), APP_DATA_DIR_NAME);
}

const targetRoot = path.join(userDataDirectory(), "live2d");

if (process.argv.includes("--list")) {
  const entries = fsSync.existsSync(targetRoot) ? await fs.readdir(targetRoot) : [];

  console.log(`模型目录：${targetRoot}`);
  console.log(entries.length > 0 ? entries.map((name) => `  - ${name}`).join("\n") : "  （空）");
  process.exit(0);
}

const sourceRoot = path.resolve(readArgument("from", defaultSource));
const modelId = readArgument("id", "default");
const source = path.join(sourceRoot, modelId);
const entryFile = path.join(source, "pet.model3.json");

if (!fsSync.existsSync(entryFile)) {
  console.error(`未找到模型入口：${entryFile}`);
  console.error("提示：模型目录里必须有固定文件名的 pet.model3.json（见 frontend/lib/live2d/model-assets.ts）");
  process.exit(1);
}

const target = path.join(targetRoot, modelId);

await fs.rm(target, { recursive: true, force: true });
await fs.mkdir(path.dirname(target), { recursive: true });
await fs.cp(source, target, { recursive: true });

const files = await fs.readdir(target);

console.log(`已安装模型「${modelId}」→ ${target}`);
console.log(`资源文件 ${files.length} 项。重启桌面端后生效（托盘 → 退出，再 npm run dev）。`);
