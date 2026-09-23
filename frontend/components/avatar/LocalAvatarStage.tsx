"use client";

/**
 * 本地渲染分派器：在「静态立绘」与「Live2D」之间选择。
 *
 * 四条设计约束：
 * 1. **探测零副作用**——只看 `public/vendor/cubism/Core/…js` 在不在（HEAD 请求），
 *    绝不 import 官方 SDK：Framework 在模块求值阶段就要读全局 `Live2DCubismCore`，
 *    顶层 import 会让 SSR 直接崩（这是实装 SDK 后踩到的真实问题）；
 * 2. **尊重手动选择**——「仅静态立绘」时连探测都不做；
 * 3. **渐进增强**——先显示静态立绘，Live2D 加载完成后淡出，避免空白期；
 * 4. **失败即回退**——模型缺失/解析失败/WebGL 不可用都退回静态立绘，
 *    原因通过 `onStatusChange` 上报给舞台统一展示（对话、语音、情绪不受影响）。
 */

import { useEffect, useRef, useState } from "react";

import type { AvatarMotion } from "@/lib/avatar/avatar-renderer";
import { BUILT_IN_MODEL_SOURCE, type AvatarModelSource } from "@/lib/avatar/avatar-model-source";
import type { AvatarRendererPreference } from "@/lib/avatar/avatar-config";
import { CUBISM_UNAVAILABLE_REASON, probeCubismCore } from "@/lib/live2d/cubism-core";
import type { Live2DLoadStage } from "@/lib/live2d/live2d-renderer";
import type { EmotionInfo, VisemeFrame } from "@/lib/api/types";

import { Live2DStage } from "./Live2DStage";
import { StaticPortraitStage } from "./StaticPortraitStage";

/** SDK 落位探测结果（pending 期间按"没有"处理，先显示静态立绘，不闪烁） */
type Availability = "pending" | "available" | "missing";

export function LocalAvatarStage({
  emotion,
  motion,
  characterLabel = "数字人形象",
  renderer = "auto",
  source = BUILT_IN_MODEL_SOURCE,
  lipSync = null,
  onStatusChange,
}: {
  emotion: EmotionInfo | null;
  motion: AvatarMotion;
  characterLabel?: string;
  /** 渲染方式偏好：static = 强制静态立绘（不探测、不加载模型） */
  renderer?: AvatarRendererPreference;
  /** 用哪个模型（内置 / 上传的 Live2D / 上传的静态立绘） */
  source?: AvatarModelSource;
  /** 口型时间轴（静态立绘不支持，会直接忽略） */
  lipSync?: readonly VisemeFrame[] | null;
  /** 状态文案上报（由舞台统一展示，避免组件内绝对定位与相邻元素重叠） */
  onStatusChange?: (status: string) => void;
}) {
  /**
   * 是否走 Live2D。
   *
   * 选了「静态立绘模型」时**不能**再挂 Live2D：否则 SDK 会把模型盖到上传的立绘上。
   */
  const wantsLive2D = renderer !== "static" && source.kind !== "images";
  const [availability, setAvailability] = useState<Availability>(
    wantsLive2D ? "pending" : "missing",
  );
  const [loadStage, setLoadStage] = useState<Live2DLoadStage>("loading");
  const [failReason, setFailReason] = useState("");
  /** 一旦失败就不再重试（SDK 与模型是构建期资源，页面生命周期内不会变） */
  const [failed, setFailed] = useState(false);

  // 回调放 ref：父组件每次渲染都会传新函数，写进依赖会让探测反复重跑
  const onStatusChangeRef = useRef(onStatusChange);
  onStatusChangeRef.current = onStatusChange;

  // ① 探测 SDK 是否落位（HEAD 静态资源，不加载 SDK）
  useEffect(() => {
    if (!wantsLive2D) return;
    let cancelled = false;
    void probeCubismCore().then((available) => {
      if (cancelled) return;
      setAvailability(available ? "available" : "missing");
      if (!available) setFailReason(CUBISM_UNAVAILABLE_REASON);
    });
    return () => {
      cancelled = true;
    };
  }, [wantsLive2D]);

  const live2dVisible = wantsLive2D && availability === "available" && !failed;
  const live2dReady = live2dVisible && loadStage === "ready";

  const statusText = !wantsLive2D
    ? "渲染：静态立绘（已选择「仅静态立绘」）"
    : live2dReady
      ? "渲染：Live2D（Cubism SDK）"
      : availability === "pending"
        ? "渲染：静态立绘（正在检测 Live2D…）"
        : availability === "missing"
          ? `渲染：静态立绘 · ${failReason}`
          : failed
            ? `渲染：静态立绘 · Live2D 不可用：${failReason}`
            : "渲染：静态立绘（Live2D 加载中…）";

  // ② 状态文案上报（字符串是原始值，可以直接作为依赖）
  useEffect(() => {
    onStatusChangeRef.current?.(statusText);
  }, [statusText]);

  return (
    <div className="relative flex h-[272px] w-[208px] items-end justify-center">
      {/* 静态立绘：永远可用，作为 Live2D 的底图与兜底 */}
      <div
        className={`absolute inset-0 flex items-end justify-center transition-opacity duration-500 ${
          live2dReady ? "pointer-events-none opacity-0" : "opacity-100"
        }`}
        aria-hidden={live2dReady}
      >
        <StaticPortraitStage
          emotion={emotion}
          motion={motion}
          characterLabel={characterLabel}
          portraitUrl={source.kind === "images" ? source.portraitUrl : null}
        />
      </div>

      {live2dVisible && (
        <div className="absolute inset-0">
          <Live2DStage
            emotion={emotion}
            motion={motion}
            characterLabel={characterLabel}
            modelUrl={source.kind === "live2d" ? source.modelUrl : null}
            modelLayout={source.kind === "live2d" ? source.layout : null}
            lipSync={lipSync}
            onStageChange={(stage, detail) => {
              setLoadStage(stage);
              if (stage === "failed") {
                setFailed(true);
                setFailReason(detail);
              }
            }}
          />
        </div>
      )}
    </div>
  );
}
