/**
 * 组装 Web 端的**可托管静态产物**（供桌面端 assetServer 直接托管）。
 *
 * ## 为什么需要这个脚本
 *
 * 发布包要带上 Web 端（完整工作台），但有三条硬约束：
 *
 * 1. **不能用 `output: "export"`**：Next 15.5 在 Windows 上导出 404 页面时会抛
 *    `EPERM: operation not permitted, open 'out/404.html'`，且该失败会**中断整个导出**
 *    ——结果是 `index.html` 根本没被生成（只能拿到 404.html 与 _next/）。
 *    已实测复现（含显式 not-found.tsx 的情况），因此改用 standalone 构建。
 *
 * 2. **不想随包分发 node.exe**：standalone 形态需要 `node server.js` 才能跑，
 *    而 node.exe 约 80MB，会让安装包突破 300MB 上限。但本项目前端**全部是预渲染页**
 *    （只有 layout + page 两个文件，无 API 路由、无服务端动态能力），
 *    所以根本不需要 Node 进程——直接把 standalone 里已经生成好的 HTML
 *    交给桌面端**已有的** assetServer 托管即可。
 *
 * 3. **发布物不得含 Live2D 模型**：`public/live2d/` 下的模型是第三方 VTube Studio
 *    资源，受版权限制且体积大（约 4.4MB）。桌面端的 verify-release-assets.mjs
 *    有一条红线会**直接拒绝**含 `live2d` 目录 / `.moc3` 文件的发布物。
 *    因此这里显式排除，用户的模型改从 `%APPDATA%/HyPRA/live2d/` 加载
 *    （见 desktop/src/main/petSettings.ts 的 petModelRoot + assetServer 的 extraRoots）。
 *
 * ## 组装结果（默认输出到 frontend/out-web/）
 *
 *     out-web/
 *       index.html            ← .next/standalone/.next/server/app/index.html
 *       _next/static/**       ← .next/static/**（Next 官方：standalone 不含它，须手动拷）
 *       portraits/            ← public/portraits（自研 SVG 立绘，可分发）
 *       vendor/               ← public/vendor（Cubism SDK 脚本，可分发）
 *       （不含 live2d/ —— 见上面第 3 条）
 *
 * 用法：
 *   node scripts/build-web-bundle.mjs          # 组装
 *   node scripts/build-web-bundle.mjs --check  # 只校验产物完整性，不组装
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const nextDir = path.join(frontendRoot, ".next");
const standAlone = path.join(nextDir, "standalone");
const publicDir = path.join(frontendRoot, "public");
const outDir = path.join(frontendRoot, "out-web");

/**
 * 需要从 standalone 里取出的预渲染 HTML。
 *
 * Next 在 `.next/server/app/` 下产出 app 路由的 HTML；`_not-found.html` 是
 * 「页面级 404」，而 `.next/server/pages/404.html` 是 Pages Router 的兜底 404
 * （两者都存在，优先用 app 的 `_not-found.html`，与路由表一致）。
 */
const HTML_SOURCES = [
  { from: path.join(standAlone, ".next/server/app/index.html"), to: "index.html" },
  { from: path.join(standAlone, ".next/server/app/_not-found.html"), to: "404.html" },
];

/**
 * 从 public/ 拷入的资源。
 *
 * ⚠️ 严格不含 `live2d`：见文件头第 3 条。这里是**白名单**而不是黑名单，
 * 这样将来 public/ 下新增目录也不会被误打包（安全默认）。
 */
const PUBLIC_WHITELIST = ["portraits", "vendor"];

function log(message) {
  process.stdout.write(`${message}\n`);
}

function fail(message) {
  process.stderr.write(`✗ ${message}\n`);
  process.exit(1);
}

/** 递归拷贝目录（目标存在则先清空，避免残留旧文件） */
function copyDir(from, to) {
  if (fs.existsSync(to)) {
    fs.rmSync(to, { recursive: true, force: true });
  }

  fs.cpSync(from, to, { recursive: true });
}

/** 统计目录体积与文件数 */
function measure(dir) {
  if (!fs.existsSync(dir)) {
    return { bytes: 0, files: 0 };
  }

  let bytes = 0;
  let files = 0;
  const stack = [dir];

  while (stack.length > 0) {
    const current = stack.pop();

    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);

      if (entry.isDirectory()) {
        stack.push(full);
      } else {
        files += 1;
        bytes += fs.statSync(full).size;
      }
    }
  }

  return { bytes, files };
}

const human = (bytes) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;

/** 校验：standalone 构建产物是否就绪 */
function ensureBuildReady() {
  if (!fs.existsSync(standAlone)) {
    fail(
      "未找到 .next/standalone —— 请先在 frontend/ 执行 `npm run build`" +
        "（next.config.mjs 的 output 必须是 \"standalone\"）",
    );
  }

  for (const { from, to } of HTML_SOURCES) {
    if (!fs.existsSync(from)) {
      fail(`缺少预渲染页面 ${to}（期望路径：${from}）—— 构建可能不完整，请重新 npm run build`);
    }
  }

  const staticSource = path.join(nextDir, "static");

  if (!fs.existsSync(staticSource)) {
    fail(`缺少 .next/static —— 前端 JS/CSS 资源在这里，缺了页面会白屏`);
  }
}

/** 只校验模式：检查已有 out-web 是否完整 */
function checkBundle() {
  if (!fs.existsSync(outDir)) {
    fail(`未找到 ${outDir} —— 请先执行 node scripts/build-web-bundle.mjs 组装`);
  }

  const indexHtml = path.join(outDir, "index.html");

  if (!fs.existsSync(indexHtml)) {
    fail("产物缺少 index.html");
  }

  // 交叉核对：HTML 里引用的每个 _next 资源都必须在产物中存在
  const html = fs.readFileSync(indexHtml, "utf8");
  const referenced = new Set(
    [...html.matchAll(/_next\/static\/[^"'\\\s)]+/g)].map((match) => match[0]),
  );
  const missing = [];

  for (const ref of referenced) {
    if (!fs.existsSync(path.join(outDir, ref))) {
      missing.push(ref);
    }
  }

  if (missing.length > 0) {
    fail(`index.html 引用了 ${missing.length} 个不存在的资源：\n  - ${missing.join("\n  - ")}`);
  }

  // 红线复查：产物中绝不能出现 Live2D 模型
  const violations = [];

  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);

      if (entry.isDirectory()) {
        if (entry.name === "live2d") {
          violations.push(path.relative(outDir, full));

          continue;
        }

        walk(full);
      } else if (/\.(moc3?|model3\.json)$/i.test(entry.name)) {
        violations.push(path.relative(outDir, full));
      }
    }
  };

  walk(outDir);

  if (violations.length > 0) {
    fail(`产物中出现受版权限制的 Live2D 资源：\n  - ${violations.join("\n  - ")}`);
  }

  const { bytes, files } = measure(outDir);
  log(`✓ Web 端产物校验通过（${files} 个文件，${human(bytes)}，引用资源 ${referenced.size} 个全部就位）`);
}

if (process.argv.includes("--check")) {
  checkBundle();
  process.exit(0);
}

log("组装 Web 端静态产物…");
ensureBuildReady();

// ① 清空重建输出目录
if (fs.existsSync(outDir)) {
  fs.rmSync(outDir, { recursive: true, force: true });
}

fs.mkdirSync(outDir, { recursive: true });

// ② 预渲染 HTML
for (const { from, to } of HTML_SOURCES) {
  fs.copyFileSync(from, path.join(outDir, to));
  log(`  + ${to}`);
}

// ③ JS/CSS 资源（Next 官方要求手动拷；standalone 里不含）
copyDir(path.join(nextDir, "static"), path.join(outDir, "_next", "static"));
log(`  + _next/static/`);

// ④ public 白名单资源（显式排除 live2d）
for (const name of PUBLIC_WHITELIST) {
  const source = path.join(publicDir, name);

  if (!fs.existsSync(source)) {
    log(`  ! 跳过 public/${name}（不存在）`);

    continue;
  }

  copyDir(source, path.join(outDir, name));
  log(`  + ${name}/`);
}

// ⑤ 组装后立即自校验（引用完整性 + 版权红线）
checkBundle();
