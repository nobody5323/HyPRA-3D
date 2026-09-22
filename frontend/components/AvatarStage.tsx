"use client";

/**
 * 数字人舞台。
 *
 * 两种渲染模式（由 provider 决定）：
 * - `xmov`：把魔珐 SDK 挂载到 `#avatar-container`（真实 3D 数字人，F2）；
 * - `browser`：占位形象 + 情绪光效（零依赖降级，F1）。
 *
 * 两种模式共享同一套外部 UI（状态徽标、角色名、提示），便于演示时无缝切换。
 */

import type { RefObject } from "react";

import type { AvatarInitStage } from "@/hooks/useAvatar";
import type { AvatarState, EmotionInfo } from "@/lib/types";
import { StateBadge } from "./StateBadge";

/** 情绪色（hex，与 tailwind.config.ts 的 mood.* 一致）：用于光晕与阴影 */
const MOOD_COLOR: Record<string, string> = {
  happy: "#E0A24E",
  calm: "#6E9BB5",
  sad: "#7E8FA8",
  anxious: "#D08B5E",
  tired: "#8B87A8",
  angry: "#C4706A",
  surprised: "#D4A94F",
  neutral: "#9A948C",
};

/**
 * hex + 透明度 → rgba。
 *
 * 修复背景：原先写作 `var(--mood-happy, #f6c177)` 再拼十六进制尾数，
 * 得到 `var(--mood-happy, #f6c177)1f` 这类**非法 CSS 值**，整条 radial-gradient 被浏览器丢弃
 * ——「情绪 → 光效」联动实际不生效；且 `--mood-*` 在项目中从未定义。
 */
function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const r = Number.parseInt(value.slice(0, 2), 16);
  const g = Number.parseInt(value.slice(2, 4), 16);
  const b = Number.parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
}

export function AvatarStage({
  state,
  emotion,
  provider,
  containerId = "avatar-container",
  containerRef,
  stage = "ready",
  detail = "",
}: {
  state: AvatarState;
  emotion: EmotionInfo | null;
  provider: "browser" | "xmov";
  containerId?: string;
  containerRef?: RefObject<HTMLDivElement | null>;
  stage?: AvatarInitStage;
  detail?: string;
}) {
  const color = MOOD_COLOR[emotion?.label ?? "neutral"] ?? MOOD_COLOR.neutral;
  const intensity = emotion?.intensity ?? 0.5;
  const isXmov = provider === "xmov";
  // 断线退避重连期间也用同一套「加载中」渲染（状态文本可读且会被播报）
  const loading =
    isXmov && (stage === "loading-sdk" || stage === "initializing" || stage === "reconnecting");

  // 容器语义：加载中保持普通容器（内部是可读的状态文本 + live region），
  // 就绪后标记为图片型区域（内部只有 SDK canvas，无文本可读）——审计 A6。
  const canvasA11y: { role?: "img"; "aria-label"?: string } = loading
    ? {}
    : { role: "img", "aria-label": "数字人舞台：魔珐星云实时渲染画面" };

  return (
    <section
      aria-labelledby="avatar-stage-title"
      className="relative flex flex-col items-center justify-center overflow-hidden rounded-2xl border border-line bg-surface-panel p-6"
    >
      {/* 区域可访问名（读屏可按区域跳转，审计 A10） */}
      <h2 id="avatar-stage-title" className="sr-only">
        数字人舞台
      </h2>

      {/* 情绪光晕（两种模式共用，SDK 画布浮在其上） */}
      {/* 强度变化用 opacity 过渡（可插值），颜色切换即时生效（渐变本身无法插值） */}
      <div
        className="pointer-events-none absolute inset-0 transition-opacity duration-1000"
        style={{
          background: `radial-gradient(circle at 50% 45%, ${withAlpha(
            color,
            0.55,
          )} 0%, transparent 65%)`,
          opacity: 0.2 + intensity * 0.6,
        }}
      />

      <div className="relative z-10 flex w-full flex-col items-center gap-5">
        {isXmov ? (
          /* 魔珐 SDK 挂载容器（真实 3D 渲染）
             底色保持深色不透明：SDK 是否自绘不透明背景无法从代码判定，
             若用浅底可能出现「透明数字人叠在浅色上」；深色画布同时是视觉焦点 */
          <div
            id={containerId}
            ref={containerRef}
            {...canvasA11y}
            className="h-[420px] w-full overflow-hidden rounded-xl bg-stage-canvas"
          >
            {loading && (
              /* 初始化中：状态文本必须可读且被播报，因此容器此时不接图片型角色 */
              <div
                role="status"
                className="flex h-full w-full flex-col items-center justify-center gap-2 text-center"
              >
                <span className="h-6 w-6 animate-spin rounded-full border-2 border-ink-on/25 border-t-ink-on" />
                <p className="text-sm text-ink-on">正在初始化数字人…</p>
                <p className="break-words px-6 text-xs leading-relaxed text-ink-on/70 line-clamp-3">
                  {detail}
                </p>
              </div>
            )}
          </div>
        ) : (
          /* 占位形象（零依赖降级）：鼠尾草色球体 + 暖灰五官（浅底上仍需可辨） */
          <div
            className={`relative h-52 w-52 rounded-full bg-gradient-to-b from-accent-soft to-accent/50 transition-transform duration-700 ${
              state === "speak" ? "animate-breathe-in" : ""
            }`}
            style={{ boxShadow: `0 0 ${20 + intensity * 50}px ${withAlpha(color, 0.33)}` }}
            role="img"
            aria-label="数字人形象占位"
          >
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="flex flex-col items-center gap-4">
                <div className="flex gap-6">
                  <span className="h-2.5 w-2.5 rounded-full bg-ink-soft" />
                  <span className="h-2.5 w-2.5 rounded-full bg-ink-soft" />
                </div>
                <span
                  className={`bg-ink-soft transition-[height,width] duration-300 ${
                    state === "speak" ? "h-4 w-8 rounded-full" : "h-1 w-6 rounded-full"
                  }`}
                />
              </div>
            </div>
          </div>
        )}

        <div className="flex flex-col items-center gap-2">
          <p className="text-lg font-medium text-ink">
            <span translate="no">苏澄</span>
          </p>
          <p className="text-xs text-ink-muted">心理倾听师 · 温柔年长的知心姐姐</p>
          <StateBadge state={state} />
        </div>

        <p className="text-xs text-ink-soft">
          {isXmov ? "渲染：魔珐星云具身驱动 SDK" : "渲染：占位形象（配置密钥后自动切换真实数字人）"}
        </p>
      </div>
    </section>
  );
}
