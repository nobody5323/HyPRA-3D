"use client";

/**
 * 数字人舞台。
 *
 * 两种渲染模式（由 provider 决定）：
 * - `xmov`：把魔珐 SDK 挂载到 `#avatar-container`（真实 3D 数字人，F2）；
 * - `browser`：静态立绘换图 + CSS 动效 + 情绪光晕（零依赖降级，F1）。
 *
 * 两种模式共享同一套外部 UI（状态徽标、角色名、提示），便于演示时无缝切换。
 */

import { useState } from "react";
import type { RefObject } from "react";

import type { AvatarController, AvatarInitStage } from "@/hooks/avatar/useAvatar";
import type { AvatarRendererPreference } from "@/lib/avatar/avatar-config";
import { BUILT_IN_MODEL_SOURCE, type AvatarModelSource } from "@/lib/avatar/avatar-model-source";
import type { AvatarState, EmotionInfo, VisemeFrame } from "@/lib/api/types";
import { LocalAvatarStage } from "./LocalAvatarStage";
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
  renderer = "auto",
  source = BUILT_IN_MODEL_SOURCE,
  lipSync = null,
  containerId = "avatar-container",
  containerRef,
  stage = "ready",
  detail = "",
}: {
  state: AvatarState;
  emotion: EmotionInfo | null;
  /**
   * 语音/形象来源。
   *
   * 直接用 `AvatarController["provider"]` 而不是手写联合类型：
   * 新增一种实现（如 server = 服务端 TTS）时只需改一处，不会漏改这里。
   * 注意 `xmov` 是唯一走 SDK 渲染的值，**其余都走本地渲染器**。
   */
  provider: AvatarController["provider"];
  /** 渲染方式偏好（仅本地渲染路径使用） */
  renderer?: AvatarRendererPreference;
  /** 用哪个模型（内置 / 上传的 Live2D / 上传的静态立绘） */
  source?: AvatarModelSource;
  /** 口型时间轴（仅本地 Live2D 渲染器消费） */
  lipSync?: readonly VisemeFrame[] | null;
  containerId?: string;
  containerRef?: RefObject<HTMLDivElement | null>;
  stage?: AvatarInitStage;
  detail?: string;
}) {
  /** 本地渲染器的状态文案（由 LocalAvatarStage 上报，这里统一展示） */
  const [localStatus, setLocalStatus] = useState("");
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
          /* 本地渲染（零依赖降级）：装了 Cubism SDK 走 Live2D，否则静态立绘。
             两者实现同一个 AvatarRenderer 接口，选择逻辑封装在 LocalAvatarStage；
             渲染方式由用户在「数字人设置」里指定（renderer），
             状态文案通过 onStatusChange 回传到这里统一展示（避免组件内绝对定位与相邻元素重叠）。 */
          <LocalAvatarStage
            emotion={emotion}
            motion={state}
            characterLabel="数字人形象"
            renderer={renderer}
            source={source}
            lipSync={lipSync}
            onStatusChange={setLocalStatus}
          />
        )}

        <div className="flex flex-col items-center gap-2">
          <p className="text-lg font-medium text-ink">
            <span translate="no">苏澄</span>
          </p>
          <p className="text-xs text-ink-muted">心理倾听师 · 温柔年长的知心姐姐</p>
          <StateBadge state={state} />
        </div>

        <p className="text-xs text-ink-soft">
          {isXmov ? "渲染：魔珐星云具身驱动 SDK" : localStatus || "渲染：本地渲染器"}
        </p>
      </div>
    </section>
  );
}
