"use client";

/**
 * 能力中心：插件体系的可见面板（设计见 `AGENTS.md §9`）。
 *
 * 数据来源：`GET /health` 的 `plugins` / `plugins_summary` 字段
 * （`backend/app/plugins/manager.py` 的 `status()` / `summary()`）。
 *
 * 为什么需要这个面板：插件体系的价值在于「能力可插拔」，但如果界面上看不见
 * 装了什么、各自什么状态、是不是只读，那它与一堆硬编码工厂没有区别。
 * 本面板把注册表摊开给用户与评审看。
 *
 * 分层语义（§9.2）：
 * - `core`        核心，不可禁用（换掉它产品就不成立）；
 * - `builtin`     内置，随项目分发，可禁用/替换；
 * - `third-party` 用户安装。
 *
 * 降级：后端未就绪（无插件数据）时**不渲染**——不制造无意义的空面板。
 */

import type { PluginStatus, PluginsSummary } from "@/lib/api/types";

const LAYER_ORDER = ["core", "builtin", "third-party"] as const;

const LAYER_LABEL: Record<string, string> = {
  core: "核心",
  builtin: "内置",
  "third-party": "第三方",
};

/** 分层配色：核心用主色强调，内置中性，第三方用警示色（提醒来源非官方）。 */
const LAYER_TONE: Record<string, string> = {
  core: "bg-accent-soft text-accent-text",
  builtin: "bg-surface-raised text-ink-muted",
  "third-party": "bg-warning-soft text-warning-text",
};

const STATE_LABEL: Record<string, string> = {
  started: "运行中",
  loaded: "已加载",
  discovered: "已发现",
  disabled: "已禁用",
  failed: "失败",
};

const STATE_TONE: Record<string, string> = {
  started: "text-success-text",
  loaded: "text-ink-muted",
  discovered: "text-ink-faint",
  disabled: "text-ink-faint",
  failed: "text-danger-text",
};

export function PluginCenter({
  plugins,
  summary,
}: {
  plugins: PluginStatus[];
  summary: PluginsSummary | null;
}) {
  if (plugins.length === 0) return null;

  return (
    <section
      aria-labelledby="plugin-center-title"
      className="rounded-lg border border-line bg-surface-panel p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="plugin-center-title" className="text-xs font-medium text-ink-muted">
          能力中心
        </h3>
        {summary && (
          <span className="text-[11px] tabular-nums text-ink-faint">
            {summary.started}/{summary.total} 运行中
            {summary.failed > 0 && <span className="text-danger-text"> · {summary.failed} 失败</span>}
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-col gap-3">
        {LAYER_ORDER.map((layer) => {
          const items = plugins.filter((plugin) => plugin.layer === layer);
          if (items.length === 0) return null;
          return (
            <div key={layer} className="flex flex-col gap-1">
              <p className="text-[11px] text-ink-faint">
                {LAYER_LABEL[layer]}（{items.length}）
              </p>
              <ul className="flex flex-col gap-1">
                {items.map((plugin) => (
                  <li
                    key={plugin.id}
                    className="flex flex-wrap items-center gap-1.5 text-[11px]"
                  >
                    <span className={`rounded px-1.5 py-0.5 ${LAYER_TONE[layer]}`}>
                      {plugin.display_name}
                    </span>
                    <span className={STATE_TONE[plugin.state]}>
                      {STATE_LABEL[plugin.state] ?? plugin.state}
                    </span>
                    {plugin.read_only && (
                      <span
                        title="只读插件：宿主未授予写句柄（由权限声明架构强制）"
                        className="text-ink-faint"
                      >
                        只读
                      </span>
                    )}
                    {plugin.capabilities.length > 0 && (
                      <span className="text-ink-faint">{plugin.capabilities.join(" · ")}</span>
                    )}
                    {plugin.error && <span className="text-danger-text">{plugin.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}
