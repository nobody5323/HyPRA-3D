/**
 * 发布包资源边界校验（构建产物与安装包）。
 *
 * 红线（与项目 .gitignore、AGENTS.md 第 6 条一致）：
 * 发布物里**不得**出现受版权限制的角色模型、声音模型、参考音频，以及任何密钥。
 * 这些内容一旦被打进包，评审拿到的东西就与仓库声明不符。
 *
 * 用法：
 *   node scripts/verify-release-assets.mjs            # 检查 dist/
 *   node scripts/verify-release-assets.mjs --release  # 连 release/ 一起检查
 */
import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/** 禁止出现的扩展名（模型 / 音频 / 声音模型 / 本地密钥配置） */
const FORBIDDEN_EXTENSIONS = new Set([
  ".moc",
  ".moc3",
  ".model3.json",
  ".motion3.json",
  ".exp3.json",
  ".physics3.json",
  ".pose3.json",
  ".cdi3.json",
  ".mtn",
  ".exp.json",
  ".pth",
  ".ckpt",
  ".wav",
  ".mp3",
  ".flac",
  ".ogg",
  ".aac",
  ".local.json",
  ".pem",
  ".key",
]);

/** 禁止出现的文件名（密钥与本地配置） */
const FORBIDDEN_NAMES = new Set([".env", ".env.local", "pet.local.json", "secure-secrets.json"]);

/** 禁止出现的目录名 */
const FORBIDDEN_DIRECTORIES = new Set(["live2d"]);

/** 允许的例外：这些扩展名即便命中上面的规则也放行（框架自带资源） */
const ALLOWED_EXTENSION_OVERRIDES = new Set([".d.ts"]);

const targets = [path.join(desktopRoot, "dist")];

if (process.argv.includes("--release")) {
  targets.push(path.join(desktopRoot, "release"));
}

/** 递归收集文件（跳过 node_modules） */
async function collectFiles(root) {
  const files = [];
  const stack = [root];

  while (stack.length > 0) {
    const current = stack.pop();
    let entries;

    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);

      if (entry.isDirectory()) {
        if (entry.name === "node_modules") continue;
        stack.push(fullPath);
        continue;
      }

      files.push(fullPath);
    }
  }

  return files;
}

/** 判定一个文件是否越界；返回原因或 null */
function violationOf(filePath) {
  const name = path.basename(filePath).toLowerCase();
  const lowerPath = filePath.toLowerCase();

  if (FORBIDDEN_NAMES.has(name)) {
    return "密钥或本地配置文件";
  }

  for (const directory of FORBIDDEN_DIRECTORIES) {
    if (lowerPath.split(path.sep).includes(directory)) {
      return `禁止的目录（${directory}）`;
    }
  }

  if (ALLOWED_EXTENSION_OVERRIDES.has(path.extname(name))) {
    return null;
  }

  // 先匹配复合扩展名（.model3.json 比 .json 更具体），否则会被后者漏掉
  for (const extension of FORBIDDEN_EXTENSIONS) {
    if (name.endsWith(extension)) {
      return `禁止的文件类型（${extension}）`;
    }
  }

  return null;
}

const violations = [];
let scanned = 0;

for (const target of targets) {
  if (!fsSync.existsSync(target)) {
    continue;
  }

  for (const filePath of await collectFiles(target)) {
    scanned += 1;
    const reason = violationOf(filePath);

    if (reason) {
      violations.push(`${path.relative(desktopRoot, filePath)} —— ${reason}`);
    }
  }
}

if (violations.length > 0) {
  console.error("✗ 发布包资源边界校验未通过：");
  console.error(violations.map((item) => `  - ${item}`).join("\n"));
  console.error("\n模型 / 声音 / 密钥一律不得进入发布物（见项目 AGENTS.md 第 6 条）。");
  process.exit(1);
}

console.log(`✓ 发布包资源边界校验通过（扫描 ${scanned} 个文件，无越界内容）`);
