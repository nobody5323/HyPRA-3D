/**
 * 复用的 frontend 代码里有 `process.env.NEXT_PUBLIC_API_BASE`。
 *
 * 那个值在 Web 端由 Next.js 注入，在桌面端由 vite 的 define 在构建期替换成字面量。
 * 这里只补一个最小的类型声明——不引入 @types/node，避免把一整套 Node 全局
 * 混进浏览器环境的类型里（那样 `setTimeout` 之类会被放宽成 Node 版本）。
 */
declare const process: {
  env: Record<string, string | undefined>;
};
