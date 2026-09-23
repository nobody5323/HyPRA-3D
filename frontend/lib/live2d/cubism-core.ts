/**
 * Cubism Core 加载器。
 *
 * 为什么需要单独一层：官方 Framework 依赖**全局** `Live2DCubismCore`
 * （Core 是 UMD 产物，不能按 ESM import），所以必须在调用任何 SDK API 之前
 * 把它以 `<script>` 注入页面。`scripts/setup-cubism.mjs` 只负责把文件复制到
 * `public/vendor/cubism/Core/`，注入是运行期的事。
 *
 * 三条约束（都对应真实故障）：
 * 1. **幂等**——React 严格模式会挂载两次，重复注入会让 Core 被初始化两遍；
 * 2. **有超时**——静态资源被拦截时必须 settle，否则界面永久停在「加载中」；
 * 3. **失败可重试**——失败时移除坏掉的 script 并清空缓存，改好文件后刷新即可。
 */

/** Core 的落位路径（与 `scripts/setup-cubism.mjs`、`.gitignore` 保持一致） */
export const CUBISM_CORE_URL = "/vendor/cubism/Core/live2dcubismcore.min.js";

/**
 * 未安装 SDK 时给用户的说明。
 *
 * 单一来源：资源探测（本文件）与降级桥（cubism-bridge.unavailable.ts）共用，
 * 避免同一种情况在两处说法不一致。
 */
export const CUBISM_UNAVAILABLE_REASON =
  "未安装 Cubism SDK：运行 `node scripts/setup-cubism.mjs <SDK目录>` 落位后即可启用 Live2D";

/**
 * 探测 Core 是否已落位（**不 import 官方 SDK**）。
 *
 * 为什么用资源探测而不是桥的 `available` 标志：官方 Framework 在**模块求值阶段**
 * 就会读全局 `Live2DCubismCore`，因此任何在模块顶层 import 它的代码，
 * 在未加载 Core（以及 SSR）时都会直接崩。探测只看文件在不在，零副作用。
 */
export async function probeCubismCore(): Promise<boolean> {
  if (isCubismCoreReady()) return true;
  if (typeof fetch !== "function") return false;
  try {
    const response = await fetch(CUBISM_CORE_URL, { method: "HEAD" });
    return response.ok;
  } catch {
    return false;
  }
}

/** 默认超时：静态资源在本地是磁盘读取，20s 足够；超时说明路径错了 */
const DEFAULT_TIMEOUT_MS = 20_000;

/** 进行中的加载（并发调用共享同一个 Promise） */
let pending: Promise<void> | null = null;

/** 读全局的 Core 对象（不依赖任何类型声明，未装 SDK 时拿到 undefined） */
function coreGlobal(): unknown {
  return (globalThis as Record<string, unknown>).Live2DCubismCore;
}

/** Core 是否已在页面上可用 */
export function isCubismCoreReady(): boolean {
  return typeof window !== "undefined" && Boolean(coreGlobal());
}

/** 加载 Core（幂等；失败可重试） */
export function loadCubismCore(options: { timeoutMs?: number; url?: string } = {}): Promise<void> {
  const url = options.url ?? CUBISM_CORE_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  // 页面上可能已经有 script 标签（或上一次已成功）→ 直接完成，不重复注入
  if (isCubismCoreReady()) return Promise.resolve();
  if (pending) return pending;

  pending = new Promise<void>((resolve, reject) => {
    if (typeof document === "undefined") {
      reject(new Error("Cubism Core 只能在浏览器环境加载"));
      return;
    }

    const script = document.createElement("script");
    script.src = url;
    script.async = true;

    let settled = false;
    const finish = (error?: Error): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      script.onload = null;
      script.onerror = null;
      if (!error) {
        resolve();
        return;
      }
      // 失败：移除坏脚本并清空缓存，允许下一次重试
      script.remove();
      pending = null;
      reject(error);
    };

    const timer = setTimeout(
      () => finish(new Error(`Cubism Core 加载超时（${timeoutMs}ms）：${url}`)),
      timeoutMs,
    );

    script.onload = () => {
      // 脚本能加载但没挂上全局（放错文件）同样算失败——否则后续报错更难定位
      finish(
        isCubismCoreReady()
          ? undefined
          : new Error("Cubism Core 脚本已加载，但没有导出 Live2DCubismCore（文件是否正确？）"),
      );
    };
    script.onerror = () =>
      finish(new Error(`Cubism Core 脚本加载失败：${url}（是否已运行 scripts/setup-cubism.mjs？）`));

    document.head.append(script);
  });

  return pending;
}

/** 清空加载缓存（**仅供测试**：生产代码不需要，也不应该调用） */
export function resetCubismCoreLoader(): void {
  pending = null;
}
