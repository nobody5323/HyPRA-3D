import { useEffect, useRef, useState } from "react";

import { resolvePortraitId, type AvatarMotion, type AvatarRenderer } from "@/lib/avatar/avatar-renderer";
import { CUBISM_UNAVAILABLE_REASON, probeCubismCore } from "@/lib/live2d/cubism-core";
import { createLive2DRenderer } from "@/lib/live2d/live2d-renderer";
import { resolveLive2DModel } from "@/lib/live2d/model-assets";
import { resolveEmotionPortrait } from "@/lib/avatar/portrait-assets";
import { createStaticPortraitRenderer } from "@/lib/avatar/static-portrait-renderer";
import type { EmotionInfo, VisemeFrame } from "@/lib/api/types";

/** SDK 落位探测结果（pending 期间先按静态立绘布局，避免闪一下） */
type Availability = "pending" | "available" | "missing";

export interface AvatarSurfaceProps {
  emotion: EmotionInfo | null;
  /** 具身状态（idle / listen / think / speak）→ 模型动作组 */
  motion: AvatarMotion;
  /** 口型时间轴（服务端 TTS 路径提供；null = 停止并闭嘴） */
  lipSync: readonly VisemeFrame[] | null;
  characterLabel: string;
  /** 渲染状态文本（"正在加载模型…" 等），由外层统一展示 */
  onStatusChange?: (status: string) => void;
}

/**
 * 桌宠的形象渲染面。
 *
 * 与 Web 端 `LocalAvatarStage` 同一套降级策略，只是外壳换成了桌宠的透明布局：
 *   Cubism 已安装 → Live2D 实时渲染；
 *   未安装 / 加载失败 → 静态立绘（`public/portraits/*.svg`），演示不中断。
 *
 * 渲染细节全在被复用的 `frontend/lib` 里，本组件只做三件事：
 * 提供 DOM 节点、把「情绪 + 状态 + 口型」翻译成渲染器调用、上报状态文本。
 */
export function AvatarSurface({
  emotion,
  motion,
  lipSync,
  characterLabel,
  onStatusChange,
}: AvatarSurfaceProps) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const rendererRef = useRef<AvatarRenderer | null>(null);

  const [availability, setAvailability] = useState<Availability>("pending");
  const [detail, setDetail] = useState("");

  // 回调放 ref：父组件每次渲染都会传新函数，写进依赖会让探测/渲染器反复重建
  const onStatusChangeRef = useRef(onStatusChange);
  onStatusChangeRef.current = onStatusChange;

  // ① 探测 Cubism Core 是否落位（HEAD 静态资源，不加载 SDK，零副作用）
  useEffect(() => {
    let cancelled = false;

    void probeCubismCore().then((available) => {
      if (cancelled) {
        return;
      }

      setAvailability(available ? "available" : "missing");

      if (!available) {
        setDetail(CUBISM_UNAVAILABLE_REASON);
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // ② 按探测结果建立渲染器（探测完成前不建，避免白建一个再销毁）
  useEffect(() => {
    if (availability === "pending") {
      return;
    }

    if (availability === "available") {
      const renderer = createLive2DRenderer({
        stage: stageRef.current,
        canvas: canvasRef.current,
        /*
         * 动态 import 官方 SDK 适配器。
         *
         * 不能放在模块顶层：官方 Framework 在模块求值阶段就读全局 `Live2DCubismCore`，
         * 而那个全局要等 Core 的 <script> 注入后才存在。渲染器保证调用本函数的时机
         * 在 Core 就绪之后。
         */
        loadBridge: async () => (await import("@cubism-bridge")).default,
        model: resolveLive2DModel(),
        onStageChange: (_stage, text) => setDetail(text),
        onError: (error) => setDetail(error.message),
      });

      rendererRef.current = renderer;

      return () => {
        renderer.dispose();
        rendererRef.current = null;
      };
    }

    // 未安装 SDK：静态立绘（零依赖，情绪换图 + CSS 动效）
    const renderer = createStaticPortraitRenderer({
      stage: stageRef.current,
      image: imageRef.current,
      onImageLoadError: (payload) => setDetail(`立绘加载失败：${payload.expression.url ?? ""}`),
    });

    rendererRef.current = renderer;

    return () => {
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [availability]);

  // ③ 情绪 → 表情。渲染器内部对同一 id 幂等，模型未就绪时会记住意图、就绪后补播。
  //    立绘走 url 换图，Live2D 只用 id（表情名由 presets.json 决定），两者共用同一份条目。
  useEffect(() => {
    rendererRef.current?.setExpression(resolveEmotionPortrait(emotion));
  }, [emotion, availability]);

  // ④ 具身状态 → 动作组
  useEffect(() => {
    rendererRef.current?.setMotion(motion);
  }, [motion, availability]);

  // ⑤ 口型时间轴（不支持口型的渲染器内部是空操作，调用方无需判断能力）
  useEffect(() => {
    rendererRef.current?.setLipSync(lipSync);
  }, [lipSync, availability]);

  // ⑥ 状态文本上报（外层用气泡或角标展示）
  useEffect(() => {
    if (availability === "pending") {
      onStatusChangeRef.current?.("正在检测渲染环境…");

      return;
    }

    if (availability === "missing") {
      onStatusChangeRef.current?.("未安装 Cubism SDK，当前使用占位立绘");

      return;
    }

    onStatusChangeRef.current?.(detail ? `正在加载 Live2D 模型：${detail}` : "");
  }, [availability, detail]);

  return (
    <div
      ref={stageRef}
      className="petAvatar"
      data-availability={availability}
      data-emotion={resolvePortraitId(emotion?.label)}
    >
      <canvas ref={canvasRef} className="petAvatarCanvas" />
      <img ref={imageRef} className="petAvatarImage" alt="" aria-hidden="true" />
    </div>
  );
}
