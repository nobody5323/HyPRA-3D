"use client";

/**
 * 静态立绘舞台：`static_portrait` 渲染器的 React 外壳。
 *
 * 组件只做两件事——把「情绪 / 具身状态」这两个**语义**喂给渲染器，以及渲染容器。
 * 预加载、令牌去竞态、`data-*` 状态、错误上报全在
 * `lib/static-portrait-renderer.ts` 里（纯 DOM，不依赖 React），
 * 这样同一份渲染逻辑将来也能被桌面端或测试直接复用。
 */

import { useEffect, useRef, useState } from "react";

import { resolvePortraitId, type AvatarMotion, type AvatarRenderer, type PortraitId } from "@/lib/avatar-renderer";
import { resolveEmotionPortrait, resolvePortraitEntry } from "@/lib/portrait-assets";
import { createStaticPortraitRenderer } from "@/lib/static-portrait-renderer";
import type { EmotionInfo } from "@/lib/types";

export function StaticPortraitStage({
  emotion,
  motion,
  characterLabel = "数字人形象",
  portraitUrl = null,
}: {
  emotion: EmotionInfo | null;
  motion: AvatarMotion;
  characterLabel?: string;
  /**
   * 外部立绘来源（上传的静态立绘模型）。
   *
   * @returns 该情绪的图片 URL；null 表示这个情绪没指定 → 回落内置占位立绘。
   */
  portraitUrl?: ((portraitId: PortraitId) => string | null) | null;
}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const rendererRef = useRef<AvatarRenderer | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // 首屏就挂 neutral 立绘：`<img>` 一开始就有 src，避免空白帧。
  // 用 useState 只算一次：这个值会写进属性，若每次渲染重算，
  // 会在“切模型”时与渲染器自己写的 src 打架。
  const [initialSrc] = useState(() => {
    const id = resolvePortraitId(null);
    return portraitUrl?.(id) ?? resolvePortraitEntry(id).url;
  });

  // ① 挂载一次：创建渲染器（后续变化由下面的 effect 驱动，不在这里重复调用）
  useEffect(() => {
    const renderer = createStaticPortraitRenderer({
      stage: stageRef.current,
      image: imageRef.current,
      onImageLoadError: ({ expression, error }) => {
        setLoadError(`${expression.name}立绘加载失败：${error.message}`);
      },
    });
    rendererRef.current = renderer;
    return () => {
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  // ② 角色名（读屏标签）
  useEffect(() => {
    rendererRef.current?.setCharacterLabel(characterLabel);
  }, [characterLabel]);

  // ③ 情绪 → 换图。`emotion` 每轮都是新对象（含 intensity），但渲染器内部对
  //    同一个 id 幂等，因此不会重复请求图片；这里顺带清掉上一次的失败提示。
  useEffect(() => {
    rendererRef.current?.setExpression(
      resolveEmotionPortrait(emotion, portraitUrl ? { portraitUrl } : undefined),
    );
    setLoadError(null);
  }, [emotion, portraitUrl]);

  // ④ 具身状态 → CSS 动效（只写 data-motion，不换图）
  useEffect(() => {
    rendererRef.current?.setMotion(motion);
  }, [motion]);

  return (
    <div className="flex flex-col items-center">
      {/*
        data-* 由渲染器接管维护；这里的初值只为「JS 未执行也能显示中性立绘」。
        aria-label 由 setCharacterLabel 写入（含渲染模式，便于读屏区分降级态）。
      */}
      <div
        ref={stageRef}
        data-visual-renderer="static_portrait"
        data-motion={motion}
        data-image-state="loading"
        className="portrait-stage relative flex h-[272px] w-[208px] items-end justify-center"
      >
        {/*
          用原生 <img> 而非 next/image：渲染器需要**直接改写 `src`**（预加载 + 令牌去竞态），
          而 next/image 的 loader / srcset 会与直接操作 DOM 冲突；立绘是 SVG 矢量图，
          本身也没有位图压缩需求。
        */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          ref={imageRef}
          src={initialSrc}
          alt={characterLabel}
          draggable={false}
          className="portrait-figure h-full w-full select-none object-contain"
        />
      </div>

      {loadError && (
        <p role="status" className="mt-1 max-w-[240px] text-center text-[11px] leading-relaxed text-danger-text">
          {loadError}
        </p>
      )}
    </div>
  );
}
