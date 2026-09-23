"use client";

/**
 * 行动层徽标：MCP 外部服务的连接状态。
 *
 * 赛题把「能办事」列为核心加分项，而此前界面上完全看不到 Agent 行动层是否
 * 真的接上了外部服务（后端 /health 早已返回 mcp 状态快照）。
 *
 * 数据来源：GET /health 的 mcp 字段（backend/app/mcp/manager.py 的 status()），
 * 每台服务器给出 name / transport / connected / tools / error。
 *
 * 未配置任何 MCP 服务器时不渲染（不制造无意义的「0/0」徽标）。
 */

import { useEffect, useRef, useState } from "react";

import type { McpServerStatus } from "@/lib/api/types";

/** 连接状态 → 徽标配色（沿用页头其余徽标的三档语义色） */
const TONE = {
  all: "bg-success-soft text-success-text ring-success/30",
  partial: "bg-warning-soft text-warning-text ring-warning/30",
  none: "bg-danger-soft text-danger-text ring-danger/30",
} as const;

export function AgentBadge({ servers }: { servers: McpServerStatus[] }) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // 展开时：点击外部或 Esc 关闭（Esc 把焦点还给触发器，键盘用户不丢位置）
  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (wrapperRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  if (servers.length === 0) return null;

  const connected = servers.filter((server) => server.connected).length;
  const total = servers.length;
  const toolCount = servers.reduce((sum, server) => sum + server.tools.length, 0);
  const tone = connected === total ? TONE.all : connected > 0 ? TONE.partial : TONE.none;

  return (
    <div ref={wrapperRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls="agent-badge-panel"
        onClick={() => setOpen((prev) => !prev)}
        title="MCP 外部工具连接状态（Agent 行动层）"
        className={`focus-ring rounded-full px-3 py-1 text-xs ring-1 transition-colors ${tone}`}
      >
        行动层 {connected}/{total} 台
        {toolCount > 0 && <span className="tabular-nums"> · {toolCount} 个工具</span>}
      </button>

      {open && (
        <div
          id="agent-badge-panel"
          className="absolute right-0 top-full z-20 mt-2 w-[300px] rounded-2xl border border-line bg-surface-panel p-3 shadow-lg"
        >
          <h3 className="text-xs font-medium text-ink">MCP 外部服务</h3>
          <ul className="mt-2 space-y-2">
            {servers.map((server) => (
              <li key={server.name} className="rounded-lg border border-line bg-surface-inset px-2.5 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span translate="no" className="break-words text-xs text-ink">
                    {server.name}
                  </span>
                  <span
                    className={`shrink-0 text-xs ${server.connected ? "text-success-text" : "text-danger-text"}`}
                  >
                    {server.connected ? "已连接" : "未连接"}
                  </span>
                </div>
                <p className="mt-1 text-xs text-ink-soft">
                  {server.transport} · {server.tools.length} 个工具
                </p>
                {server.tools.length > 0 && (
                  <p translate="no" className="mt-0.5 break-words text-xs text-ink-faint">
                    {server.tools.join("、")}
                  </p>
                )}
                {server.error && (
                  <p className="mt-1 break-words text-xs leading-relaxed text-danger-text">
                    {server.error}
                  </p>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs leading-relaxed text-ink-faint">
            连接失败不影响对话：该服务器的工具只是不出现在模型可选工具里。
          </p>
        </div>
      )}
    </div>
  );
}
