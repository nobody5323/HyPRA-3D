"use client";

/**
 * 能力中心：插件体系的可见 + 可操作面板（设计见 `AGENTS.md §9`）。
 *
 * 列表数据来自 `GET /health` 的 `plugins` / `plugins_summary`
 * （`backend/app/plugins/manager.py` 的 `status()` / `summary()`）——与页面已有的
 * 轮询共用一条来源，不再另开 `/plugins` 拉同一份数据。
 *
 * 为什么不只做只读展示：插件体系的价值在于「能力可插拔」。如果界面上看不见装了什么、
 * 不能启停、不能配置，那它与一堆硬编码工厂没有区别——展示层缺位会让整层架构白做。
 * 详情（配置 / 启停）**按需加载**，收起时不发请求。
 *
 * 分层语义（§9.2）：
 * - `core`        核心，不可禁用（换掉它产品就不成立）；
 * - `builtin`     内置，随项目分发，可禁用/替换；
 * - `third-party` 用户安装。
 *
 * 降级：后端未就绪（无插件数据）时**不渲染**——不制造无意义的空面板。
 */

import { useState } from "react";

import { PluginDetail } from "@/components/settings/PluginDetail";
import { TavernImportPanel } from "@/components/settings/TavernImportPanel";
import type { PluginStatus, PluginsSummary } from "@/lib/api/types";

/**
 * 酒馆接入插件的 id。
 *
 * 这里对具体插件 id 做一次判断，是为了把「记忆导入」这个**跨层动作**（会话 → 记忆）
 * 挂到能力中心里。插件体系本身不提供 UI 挂载点（§9.7 明确不做 globalUI），
 * 所以宿主知道这一个 id 是当前最小的耦合——将来接入更多数据源时，
 * 应改为按 `datasource` 能力驱动，而不是继续加 id 判断。
 */
const TAVERN_PLUGIN_ID = "tavern-bridge";

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
  companionId = "",
  onPluginsChanged,
}: {
  plugins: PluginStatus[];
  summary: PluginsSummary | null;
  /** 当前陪伴对象；酒馆记忆导入按它隔离（§8.2 的 `companion:{id}`） */
  companionId?: string;
  /** 启停 / 保存后回传最新快照，页面据此更新列表 */
  onPluginsChanged?: (plugins: PluginStatus[]) => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  if (plugins.length === 0) return null;

  const hasTavernBridge = plugins.some((plugin) => plugin.id === TAVERN_PLUGIN_ID);

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
                {items.map((plugin) => {
                  const open = openId === plugin.id;
                  return (
                    <li key={plugin.id}>
                      <button
                        type="button"
                        aria-expanded={open}
                        className="focus-ring flex w-full flex-wrap items-center gap-1.5 rounded text-left text-[11px] hover:bg-surface-hover"
                        onClick={() => setOpenId(open ? null : plugin.id)}
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
                        <span aria-hidden className="ml-auto text-ink-faint">
                          {open ? "收起" : "详情"}
                        </span>
                      </button>
                      {open && (
                        <PluginDetail plugin={plugin} onPluginsChanged={onPluginsChanged} />
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>

      {hasTavernBridge && (
        <div className="mt-3">
          <TavernImportPanel companionId={companionId} />
        </div>
      )}
    </section>
  );
}
