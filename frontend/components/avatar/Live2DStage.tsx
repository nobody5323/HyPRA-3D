"use client";

/**
 * Live2D 舞台：`createLive2DRenderer` 的 React 外壳。
 *
 * 组件只负责三件事：提供 canvas/stage 节点、把「情绪 + 具身状态」翻译成渲染器调用、
 * 把加载阶段上报给父组件（父组件据此决定要不要继续显示静态立绘）。
 * 渲染细节全在 `lib/live2d/live2d-renderer.ts` 里（纯 DOM，可单测）。
 */

import { useEffect, useRef, useState } from "react";

import { resolvePortraitId, type AvatarMotion } from "@/lib/avatar/avatar-renderer";
import {
  createLive2DRenderer,
  type Live2DLoadStage,
  type Live2DRenderer,
} from "@/lib/live2d/live2d-renderer";
import { resolveLive2DModel } from "@/lib/live2d/model-assets";
import { PORTRAIT_LABELS } from "@/lib/avatar/portrait-assets";
import type { EmotionInfo, VisemeFrame } from "@/lib/api/types";

export function Live2DStage({
  emotion,
  motion,
  characterLabel = "数字人形象",
  modelUrl = null,
  lipSync = null,
  onStageChange,
  onError,
}: {
  emotion: EmotionInfo | null;
  motion: AvatarMotion;
  characterLabel?: string;
  /** 外部模型入口 URL（上传的模型）；null = 项目内置模型 */
  modelUrl?: string | null;
  /** 口型时间轴（后端 viseme）；`null` = 停止并闭嘴 */
  lipSync?: readonly VisemeFrame[] | null;
  onStageChange?: (stage: Live2DLoadStage, detail: string) => void;
  onError?: (error: Error) => void;
}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<Live2DRenderer | null>(null);

  // 回调放进 ref：父组件每次渲染都会传新函数，写进依赖会导致渲染器被反复重建
  const onStageChangeRef = useRef(onStageChange);
  onStageChangeRef.current = onStageChange;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const [loadStage, setLoadStage] = useState<Live2DLoadStage>("idle");
  const [detail, setDetail] = useState("");

  // ① 挂载一次：创建渲染器（内部自行发起模型加载）
  useEffect(() => {
    const renderer = createLive2DRenderer({
      stage: stageRef.current,
      canvas: canvasRef.current,
      /*
       * 动态 import 官方 SDK 适配器。
       *
       * 不能放在模块顶层：官方 Framework 在模块求值阶段就读全局
       * `Live2DCubismCore`，而那个全局要等 Core 的 <script> 注入后才存在。
       * 渲染器保证调用本函数的时机在 Core 就绪之后。
       */
      loadBridge: async () => (await import("@cubism-bridge")).default,
      // 上传的模型用它的入口 URL；没有则用内置模型
      model: modelUrl
        ? {
            id: "uploaded",
            name: "上传的模型",
            modelUrl,
            layout: { scale: 1, anchor: "bottom center" },
          }
        : resolveLive2DModel(),
      onError: (error) => onErrorRef.current?.(error),
      onStageChange: (stage, nextDetail) => {
        setLoadStage(stage);
        setDetail(nextDetail);
        onStageChangeRef.current?.(stage, nextDetail);
      },
    });
    rendererRef.current = renderer;
    return () => {
      renderer.dispose();
      rendererRef.current = null;
    };
    // 换模型必须重建渲染器（贴图/物理/表情全部来自模型包），故把 modelUrl 列入依赖
  }, [modelUrl]);

  // ② 角色名（读屏标签）
  useEffect(() => {
    rendererRef.current?.setCharacterLabel(characterLabel);
  }, [characterLabel]);

  // ③ 情绪 → 模型表情。传的是「表情意图」（情绪 id），模型表情名由 presets.json 决定；
  //    渲染器内部对同一 id 幂等，且模型未就绪时会记住意图、就绪后补播。
  useEffect(() => {
    const portraitId = resolvePortraitId(emotion?.label);
    rendererRef.current?.setExpression({ id: portraitId, name: PORTRAIT_LABELS[portraitId] });
  }, [emotion]);

  // ④ 具身状态 → 动作组
  useEffect(() => {
    rendererRef.current?.setMotion(motion);
  }, [motion]);

  // ⑤ 口型时间轴（null = 停止并闭嘴；渲染器内部会在 update 前采样）
  useEffect(() => {
    rendererRef.current?.setLipSync(lipSync ?? null);
  }, [lipSync]);

  return (
    <div ref={stageRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        className="live2d-canvas block h-full w-full"
        role="img"
        aria-label={`${characterLabel}：Live2D 实时渲染画面`}
      />
      {loadStage === "loading" && (
        <div
          role="status"
          className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center"
        >
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
          <p className="text-xs text-ink-muted">正在加载 Live2D 模型…</p>
          {detail && (
            <p className="max-w-[220px] break-words text-[11px] leading-relaxed text-ink-soft line-clamp-3">
              {detail}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
