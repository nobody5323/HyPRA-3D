import { useEffect, useRef, useState } from "react";

import { resolvePortraitId, type AvatarMotion, type AvatarRenderer } from "@/lib/avatar/avatar-renderer";
import { resolveAvatarModelSource } from "@/lib/avatar/avatar-model-source";
import { CUBISM_UNAVAILABLE_REASON, probeCubismCore } from "@/lib/live2d/cubism-core";
import { createLive2DRenderer } from "@/lib/live2d/live2d-renderer";
import { resolveLive2DModel } from "@/lib/live2d/model-assets";
import { resolveEmotionPortrait } from "@/lib/avatar/portrait-assets";
import { createStaticPortraitRenderer } from "@/lib/avatar/static-portrait-renderer";
import { useAvatarModels } from "@/hooks/avatar/useAvatarModels";
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
  /**
   * 实际生效的渲染种类。
   *
   * 外层据此决定要不要画「圆底座 + 情绪光晕」：那套外观是给**没有模型时的占位形象**
   * 准备的，套在真实 Live2D 模型上就是一块挡住模型的淡黄背景。
   */
  onAvailabilityChange?: (kind: AvatarRenderKind) => void;
}

/** 实际生效的渲染种类（`live2d` = 真模型；`portrait` = 静态立绘） */
export type AvatarRenderKind = "live2d" | "portrait";

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
  onAvailabilityChange,
}: AvatarSurfaceProps) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const rendererRef = useRef<AvatarRenderer | null>(null);

  const [availability, setAvailability] = useState<Availability>("pending");
  const [detail, setDetail] = useState("");

  /*
   * 用哪个模型：与 Web 端**同一套选择**（`useAvatarModels` 拉后端存的 selection）。
   *
   * 不自己存一份的理由：桌宠窗是 `127.0.0.1:34567`、Web 端是 `localhost:3000`，
   * localStorage 天然不共享；而用户在控制台里调好的模型与构图必须是两端一致的。
   */
  const avatarModels = useAvatarModels();
  const source = resolveAvatarModelSource(avatarModels.selected);

  /** 渲染器重建的判据：换模型必须重建（贴图/物理/表情全来自模型包），换构图不用 */
  const modelKey = source.kind === "live2d" ? source.modelUrl : source.kind;
  /** 构图校准（null = 未校准）；`builtin` 分支的构图在 model-assets 里 */
  const modelLayout = source.kind === "builtin" ? null : source.layout;

  // 渲染器创建 effect 只应因「换模型」重建，所以取当前值走 ref
  const sourceRef = useRef(source);
  sourceRef.current = source;

  // 回调放 ref：父组件每次渲染都会传新函数，写进依赖会让探测/渲染器反复重建
  const onStatusChangeRef = useRef(onStatusChange);
  onStatusChangeRef.current = onStatusChange;
  const onAvailabilityChangeRef = useRef(onAvailabilityChange);
  onAvailabilityChangeRef.current = onAvailabilityChange;

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

    // 选了「静态立绘模型」时不能再挂 Live2D：SDK 会把模型盖到上传的立绘上
    const current = sourceRef.current;
    const wantsLive2D = availability === "available" && current.kind !== "images";

    if (wantsLive2D) {
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
        // 上传的模型用它自己的入口 URL + 后端存的构图校准；没有则用内置模型
        model:
          current.kind === "live2d"
            ? {
                id: "uploaded",
                name: "上传的模型",
                modelUrl: current.modelUrl,
                layout: current.layout ?? undefined,
              }
            : resolveLive2DModel(),
        onStageChange: (_stage, text) => setDetail(text),
        onError: (error) => setDetail(error.message),
      });

      rendererRef.current = renderer;

      return () => {
        renderer.dispose();
        rendererRef.current = null;
      };
    }

    // 未安装 SDK / 选了立绘模型：静态立绘（零依赖，情绪换图 + CSS 动效）
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
  }, [availability, modelKey]);

  // ②b 构图校准（在控制台里调好的）→ 就地应用，不重建渲染器（重建会重新下载模型）
  useEffect(() => {
    rendererRef.current?.setPortraitLayout(modelLayout ?? null);
  }, [modelLayout]);

  // ③ 情绪 → 表情。渲染器内部对同一 id 幂等，模型未就绪时会记住意图、就绪后补播。
  //    立绘走 url 换图，Live2D 只用 id（表情名由 presets.json 决定），两者共用同一份条目。
  useEffect(() => {
    const portrait = resolveEmotionPortrait(emotion);
    // 选了上传的静态立绘模型：用它的图；该情绪没配图则回落内置占位立绘
    const current = sourceRef.current;
    const customUrl =
      current.kind === "images" ? current.portraitUrl(resolvePortraitId(emotion?.label)) : null;

    rendererRef.current?.setExpression(customUrl ? { ...portrait, url: customUrl } : portrait);
  }, [emotion, availability, modelKey]);

  // ④ 具身状态 → 动作组
  useEffect(() => {
    rendererRef.current?.setMotion(motion);
  }, [motion, availability]);

  // ⑤ 口型时间轴（不支持口型的渲染器内部是空操作，调用方无需判断能力）
  useEffect(() => {
    rendererRef.current?.setLipSync(lipSync);
  }, [lipSync, availability]);

  // ⑥b 上报实际渲染种类（外层据此决定是否显示底座与光晕）
  useEffect(() => {
    // 选了立绘模型时即使 SDK 可用也是立绘，所以不能只看 availability
    const live2dActive = availability === "available" && source.kind !== "images";
    onAvailabilityChangeRef.current?.(live2dActive ? "live2d" : "portrait");
  }, [availability, source.kind]);

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
