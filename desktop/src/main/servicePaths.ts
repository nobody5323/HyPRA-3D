import fs from "node:fs";
import path from "node:path";

/**
 * 受管服务的路径解析。
 *
 * 这里只做「路径推导」与「存在性判断」，**刻意不 import electron**：
 * 起始目录由调用方给（主进程传 `__dirname`、exe 目录等），
 * 这样整个模块能在 vitest 里直接测（与 `ipcValidation.ts` 同样的做法）。
 */

/** 一次服务启动需要的全部路径 */
export interface ServicePaths {
  /** 项目根（开发形态：含 `.venv` 与 `_local/qdrant` 的目录；打包形态：resources 目录） */
  projectRoot: string;
  /**
   * 后端可执行文件。
   *
   * - 开发形态：`.venv/Scripts/python.exe`（由 `buildServiceCommand` 拼 `-m uvicorn` 启动）
   * - 打包形态：随包分发的 `backend.exe`（它自己就会起 uvicorn，不带参数）
   *
   * 字段名沿用 `pythonExe`，以免改动既有设置项与服务设置界面的路径覆盖语义；
   * 两种形态的差别由 `packaged` 字段区分。
   */
  pythonExe: string;
  /** Qdrant 可执行文件 */
  qdrantExe: string;
  /** Qdrant 工作目录（storage / snapshots 落在这里，与手动启动时的行为一致） */
  qdrantDir: string;
  /** 后端工作目录（uvicorn 的 cwd，`app` 包就在这里） */
  backendDir: string;
  /**
   * Web 前端目录（Next 项目的根，`npm run dev` 的 cwd）。
   *
   * 打包形态下它**指向一个并不存在的目录**：发布包不随包分发 Web 端
   * （见 `electron-builder` 的 `extraResources`），所以那种形态下前端服务
   * 会直接报「发布包不含 Web 端」。这不是漏配，是刻意的边界——
   * 桌面端有自己的渲染层，Web 端是另一种部署形态。
   */
  frontendDir: string;
  /**
   * 是否来自随包资源（打包形态）。
   *
   * 它决定两件事：后端用 `backend.exe` 直启（不拼 `-m uvicorn`）；
   * 以及 Qdrant 的数据目录必须落在**用户可写**目录，而不是只读的 `resources/`。
   */
  packaged: boolean;
}

/** 用户在设置里填的路径覆盖（空串 = 不覆盖） */
export interface PathOverrides {
  projectRoot?: string;
  pythonPath?: string;
  qdrantPath?: string;
}

/** 由项目根推导各服务路径（纯函数，不做任何 IO） */
export function deriveServicePaths(projectRoot: string): ServicePaths {
  const root = path.resolve(projectRoot);

  return {
    projectRoot: root,
    pythonExe: path.join(root, ".venv", "Scripts", "python.exe"),
    qdrantExe: path.join(root, "_local", "qdrant", "qdrant.exe"),
    qdrantDir: path.join(root, "_local", "qdrant"),
    backendDir: path.join(root, "backend"),
    frontendDir: path.join(root, "frontend"),
    packaged: false,
  };
}

/**
 * 由随包资源与用户数据目录推导路径（打包形态）。
 *
 * 与开发形态的两处关键差异：
 *
 * 1. 后端不再需要 Python 解释器——`backend.exe` 是自带运行时的 PyInstaller 产物；
 * 2. **Qdrant 的数据目录必须落在用户可写目录**。`resources/` 在安装目录下
 *    （Windows 通常在 `Program Files`），普通用户没有写权限；而 Qdrant 把
 *    `storage` / `snapshots` 落在 cwd 下，所以 cwd（`qdrantDir`）取 userData，
 *    而不是可执行文件旁边。
 */
export function derivePackagedPaths(options: {
  /** 安装目录下的 `resources/`（Electron 的 `process.resourcesPath`） */
  resourcesPath: string;
  /** 用户数据目录（Electron 的 `app.getPath("userData")`） */
  userDataDir: string;
}): ServicePaths {
  const resources = path.resolve(options.resourcesPath);
  const userData = path.resolve(options.userDataDir);

  return {
    projectRoot: resources,
    pythonExe: path.join(resources, "backend", "backend.exe"),
    qdrantExe: path.join(resources, "qdrant", "qdrant.exe"),
    qdrantDir: path.join(userData, "qdrant"),
    backendDir: path.join(resources, "backend"),
    frontendDir: path.join(resources, "frontend"),
    packaged: true,
  };
}

/**
 * 套用路径覆盖（只覆盖填了的项）。
 *
 * Qdrant 的工作目录跟着可执行文件走：用户把 qdrant.exe 换到别处时，
 * storage 也应该落在它旁边，否则会出现「数据在旧目录、程序在新目录」的分裂。
 */
export function applyOverrides(base: ServicePaths, overrides: PathOverrides): ServicePaths {
  const qdrantExe = overrides.qdrantPath ? path.resolve(overrides.qdrantPath) : base.qdrantExe;

  return {
    ...base,
    pythonExe: overrides.pythonPath ? path.resolve(overrides.pythonPath) : base.pythonExe,
    qdrantExe,
    qdrantDir: overrides.qdrantPath ? path.dirname(qdrantExe) : base.qdrantDir,
  };
}

/**
 * 判断某个目录是不是 HyPRA 项目根。
 *
 * 判据是「两样必需的东西都在」：`.venv` 的 Python 与 `_local/qdrant` 的 Qdrant。
 * 只看 `.venv` 会把别的 Python 项目误判成本项目。
 */
export function isProjectRoot(dir: string, exists: (target: string) => boolean): boolean {
  const paths = deriveServicePaths(dir);

  return exists(paths.pythonExe) && exists(paths.qdrantExe);
}

/**
 * 从若干起始目录逐级向上找项目根。
 *
 * `exists` 注入是为了可测：测试不必真造出 `.venv` 目录树。
 * 返回 null 表示「没找到」——由调用方决定是报错还是回落到用户的路径覆盖。
 */
export function findProjectRoot(
  startDirs: string[],
  exists: (target: string) => boolean,
  maxLevels = 6,
): string | null {
  for (const start of startDirs) {
    let current = path.resolve(start);

    for (let level = 0; level < maxLevels; level += 1) {
      if (isProjectRoot(current, exists)) {
        return current;
      }

      const parent = path.dirname(current);

      if (parent === current) {
        break; // 已到盘根，再往上还是它自己
      }

      current = parent;
    }
  }

  return null;
}

/** 默认的存在性判断（真实文件系统） */
export function fileExists(target: string): boolean {
  try {
    return fs.existsSync(target);
  } catch {
    return false;
  }
}

/**
 * 解析最终使用的路径。
 *
 * 返回 null 表示「找不到项目」——代管不成立，调用方应给出可读的失败说明，
 * 而不是硬编码一堆可能不存在的路径去 spawn。
 */
export function resolveServicePaths(options: {
  /** 起始搜索目录（按顺序尝试） */
  searchFrom: string[];
  overrides: PathOverrides;
  /** 存在性判断（默认走 fs，测试可注入） */
  exists?: (target: string) => boolean;
  /**
   * 打包形态的资源/用户目录；`null` 或省略 = 开发形态（沿项目根探测）。
   *
   * 打包形态**不做项目根探测**：那时程序自带 backend/qdrant，探测只会去
   * 安装目录里瞎找，反而可能命中无关目录。也因此 `overrides.projectRoot`
   * 在打包形态下不生效（该形态的路径由安装位置决定），
   * 但 `pythonPath` / `qdrantPath` 两项单项覆盖仍然有效。
   */
  packaged?: { resourcesPath: string; userDataDir: string } | null;
}): ServicePaths | null {
  const exists = options.exists ?? fileExists;

  if (options.packaged) {
    return applyOverrides(derivePackagedPaths(options.packaged), options.overrides);
  }

  // 用户手填了项目根：以它为准（填错就在启动时报出具体缺哪一项，而不是悄悄换到别的目录）
  if (options.overrides.projectRoot) {
    return applyOverrides(deriveServicePaths(options.overrides.projectRoot), options.overrides);
  }

  const found = findProjectRoot(options.searchFrom, exists);

  if (!found) {
    return null;
  }

  return applyOverrides(deriveServicePaths(found), options.overrides);
}

/**
 * 检查这批路径是否真能用来启动**向量库 / 后端**。
 *
 * 返回第一处缺失的可读描述（null = 齐了）。有了它，界面能直接说出
 * 「找不到 Python 解释器：<路径>」（打包形态则是「找不到后端可执行文件」），
 * 而不是笼统的「启动失败」。
 *
 * 前端不走这里——它既不需要 Python 也不需要 Qdrant，见 `describeMissingFrontendPath`。
 */
export function describeMissingPath(
  paths: ServicePaths,
  exists: (target: string) => boolean,
): string | null {
  if (!exists(paths.pythonExe)) {
    return paths.packaged
      ? `找不到后端可执行文件：${paths.pythonExe}（安装包可能不完整，建议重新安装）`
      : `找不到 Python 解释器：${paths.pythonExe}（可在服务设置里指定项目根或解释器路径）`;
  }

  if (!exists(paths.qdrantExe)) {
    return `找不到 Qdrant 可执行文件：${paths.qdrantExe}（可在服务设置里指定路径）`;
  }

  if (!exists(paths.backendDir)) {
    return `找不到后端目录：${paths.backendDir}`;
  }

  return null;
}

/**
 * 检查这批路径是否真能用来启动 **Web 前端**。
 *
 * 判据与后端/向量库完全不同：前端是个 Node 项目，需要的是项目目录本身
 * 与它自己装好的依赖，与 Python、Qdrant 一概无关。
 *
 * `node_modules` 必须单独查：`npm run dev` 在依赖缺失时**能起进程但立刻退出**，
 * 报出来的是一句难懂的模块解析错误；在这里先拦住，界面就能直接说清
 * 「先 cd frontend 再 npm install」。
 */
export function describeMissingFrontendPath(
  paths: ServicePaths,
  exists: (target: string) => boolean,
): string | null {
  if (!exists(paths.frontendDir)) {
    return paths.packaged
      ? "发布包不含 Web 端（Web 端是另一种部署形态，需单独部署后在「Web 模式」里填地址）"
      : `找不到 Web 前端目录：${paths.frontendDir}（可在服务设置里指定项目根）`;
  }

  if (!exists(path.join(paths.frontendDir, "node_modules"))) {
    return `Web 前端依赖未安装：${path.join(paths.frontendDir, "node_modules")} 不存在（先 cd frontend 再 npm install）`;
  }

  return null;
}
