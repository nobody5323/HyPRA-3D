/**
 * 显式 404 页面。
 *
 * 为什么需要它：`next build` 配 `output: "export"` 时，若 `app/` 下没有
 * `not-found.tsx`，Next 会走**隐式** `/_not-found` 处理，并在 Windows 上
 * 出现 `EPERM: operation not permitted, open 'out/404.html'` —— 写 404.html
 * 失败会中断整个导出流程，导致 `index.html` 根本没被生成。
 *
 * 显式声明后，404 走正常 prerender 路径，该问题不再复现。
 *
 * 语气与主站一致：安静、不吓人，给一个回首页的出口。
 */
import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-2xl font-medium text-ink">这里没有内容</h1>
      <p className="text-sm text-ink-muted">页面可能已被移动或删除。</p>
      <Link
        href="/"
        className="mt-2 rounded-lg border border-line px-4 py-2 text-sm text-ink transition hover:bg-surface-raised"
      >
        回到首页
      </Link>
    </main>
  );
}
