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
  characterName = "",
  characterTitle = "",
  className,
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
  /**
   * 当前角色名与一句话定位（来自当前人设）。
   *
   * 空值时这两行**不渲染**，而不是退回某个具体角色名：内置人设是示例，
   * 换人设后还显示「苏澄」会让演示与自建角色对不上。
   */
  characterName?: string;
  characterTitle?: string;
  /**
   * 额外类名。
   *
   * 存在的理由：Web 端希望舞台**撑满左栏剩余高度**（`flex-1`），
   * 而桌宠/控制台预览这类场景只想要它自然高度——尺寸交给调用方决定，
   * 组件本身不自作主张。
   */
  className?: string;
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

  const rendererLabel = isXmov ? "渲染：魔珐星云具身驱动 SDK" : localStatus || "渲染：本地渲染器";

  return (
    <section
      aria-labelledby="avatar-stage-title"
      className={`relative flex flex-col overflow-hidden rounded-2xl border border-line bg-surface-panel shadow-card ${className ?? ""}`}
    >
      {/* 区域可访问名（读屏可按区域跳转，审计 A10） */}
      <h2 id="avatar-stage-title" className="sr-only">
        数字人舞台
      </h2>

      {/*
        舞台光影。三层叠加，全部 pointer-events-none：
        ① 情绪光晕（颜色随情绪变、强度随 intensity 变）——这是「情绪 → 画面」的直观证据；
        ② 顶部冷光：模拟上方光源，让角色背后不是一块死白；
        ③ 地面投影：底部一道压暗，把角色「放」在地面上而不是浮在卡片中央。
        强度变化用 opacity 过渡（可插值），颜色切换即时生效（渐变本身无法插值）。
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 transition-opacity duration-1000"
        style={{
          background: `radial-gradient(circle at 50% 42%, ${withAlpha(color, 0.55)} 0%, transparent 62%)`,
          opacity: 0.18 + intensity * 0.55,
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-surface-inset/70 to-transparent"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-surface-raised to-transparent"
      />

      {/* 舞台主体：居中承载画面与角色信息，撑满卡片剩余高度 */}
      <div className="relative z-10 flex min-h-0 flex-1 flex-col items-center justify-center gap-5 px-6 py-7">
        {isXmov ? (
          /* 魔珐 SDK 挂载容器（真实 3D 渲染）
             底色保持深色不透明：SDK 是否自绘不透明背景无法从代码判定，
             若用浅底可能出现「透明数字人叠在浅色上」；深色画布同时是视觉焦点 */
          <div
            id={containerId}
            ref={containerRef}
            {...canvasA11y}
            className="h-[420px] w-full max-w-[560px] overflow-hidden rounded-xl bg-stage-canvas shadow-panel ring-1 ring-line/60"
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
          <div className="relative flex items-end justify-center">
            {/*
              舞台底纹：两圈同心的极淡描边环。
              存在的理由很实际——本地渲染器的人物是固定尺寸（208×272），
              在高屏上舞台会空出一大片；没有底纹时那片空白看起来像「没加载出来」，
              有环之后它就是「舞台」。环用极低透明度的暖灰（与 ink 同族），不抢人物。
            */}
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-1/2 top-1/2 h-[300px] w-[300px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-[rgba(47,42,38,0.07)]"
            />
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-1/2 top-1/2 h-[420px] w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-[rgba(47,42,38,0.05)]"
            />
            {/* 地面：一道压扁的径向阴影，让角色站在台上而不是浮着 */}
            <span
              aria-hidden="true"
              className="pointer-events-none absolute -bottom-1 h-6 w-44 rounded-[50%] bg-ink/15 blur-md"
            />
            <LocalAvatarStage
              emotion={emotion}
              motion={state}
              characterLabel="数字人形象"
              renderer={renderer}
              source={source}
              lipSync={lipSync}
              onStatusChange={setLocalStatus}
            />
          </div>
        )}

        {/* 角色信息：名字给到最大字号（这是这一屏的标题），定位与状态依次降级 */}
        <div className="flex flex-col items-center gap-2">
          {characterName ? (
            <p className="text-xl font-semibold tracking-tight text-ink">
              <span translate="no">{characterName}</span>
            </p>
          ) : null}
          {characterTitle ? (
            <p className="max-w-[36ch] text-center text-xs leading-relaxed text-ink-muted">
              {characterTitle}
            </p>
          ) : null}
          <StateBadge state={state} />
        </div>
      </div>

      {/* 渲染路径：贴底一条细状态栏，不占中间的视觉重量 */}
      <footer className="relative z-10 shrink-0 border-t border-line/70 bg-surface-panel/60 px-4 py-2 text-center text-[11px] leading-relaxed text-ink-soft backdrop-blur-sm">
        {rendererLabel}
      </footer>
    </section>
  );
}
