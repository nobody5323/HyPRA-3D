"use client";

/**
 * 锚定浮层的定位 —— 四个切换器（人设 / 文风 / 模型预设 / 叙事框架）共用。
 *
 * ## 为什么不能用 `absolute`
 *
 * 四个切换器原先都把浮层写成 `absolute z-20 mt-1 w-full`，锚点是包着触发器的
 * `relative`。这在 Web 端的页签里能用，但**只要祖先里有一层 `overflow` 不是
 * `visible`，浮层就会被裁掉**——程序控制台把整个面板包在
 * `<Card className="card overflow-hidden">` 里（`ConsoleApp.tsx`），于是四个下拉
 * 全都只露出顶上一条边，用户看到的就是「点不开 / 拉不下来」。
 *
 * ## 为什么是 `position: fixed` 而不是 portal
 *
 * `fixed` 的包含块是**视口**，而按 CSS Overflow 的规定，`overflow` 只裁剪
 * 「包含块在裁剪元素内部」的后代；视口是裁剪元素的祖先，所以 fixed 元素**逃逸**
 * 祖先的一切 `overflow`。这一条同时解决了 `Card` 的 `overflow-hidden` 与
 * `<main class="consoleScroll overflow-y-auto">` 的裁剪。
 *
 * 相比把浮层搬进 `createPortal(document.body)`，留在原地的好处很实在：浮层仍在
 * 原来的 React 子树里，焦点、事件冒泡、以及各切换器「点击外部关闭」用的
 * `panelRef.current.contains(target)` 命中判定**全都不用改**。
 *
 * ⚠️ 前提：祖先里不能有 `transform` / `filter` / `will-change` / `contain`——
 * 那些会让该祖先变成 fixed 的包含块，裁剪就回来了。本项目的面板外壳没有这些
 * 属性（`.card` 只有圆角、边框与阴影；`globals.css` 里的 `transform` 全部在
 * `@keyframes` 内）。**日后给面板外壳加动效时要留意这一条。**
 */

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import type { CSSProperties, RefObject } from "react";

/** 浮层与触发器之间的间距（沿用原先 `mt-1` 的 4px） */
const GAP = 4;
/** 与视口边缘至少留出的距离 */
const VIEWPORT_MARGIN = 8;
/**
 * 下方剩余空间小于这个值就改**向上弹**。
 *
 * 控制台窗最小高度只有 560px（`consoleWindow.ts`），人设与文风那一屏在最小高度下
 * 会滚动，靠下的切换器下方往往只剩一百来像素——继续向下弹就只能露出一条缝。
 */
const FLIP_THRESHOLD = 220;
/** 浮层自身的最大高度上限（列表再长也不至于占满整屏） */
const MAX_HEIGHT = 420;
/** 下限：实在没地方也保证能露出一截，靠浮层内部滚动看 */
const MIN_HEIGHT = 140;

/**
 * 未完成首次测量时的占位样式。
 *
 * `absolute` 让浮层**不参与流式布局**（避免它把后面的控件顶下去、进而让锚点
 * 位置在测量前后不一致），`visibility: hidden` 则保证这一帧不会被看到。
 * 首次测量放在 `useLayoutEffect` 里，所以这一帧不会真的绘制出来。
 */
const UNMEASURED: CSSProperties = { position: "absolute", visibility: "hidden" };

/**
 * `useLayoutEffect` 在服务端渲染时会告警（React 的既定行为），而本组件是
 * `"use client"`、Next.js 仍会在服务端预渲染一次——所以服务端退化成 `useEffect`
 * （服务端本来也没有布局可言，退化了也无害）。
 */
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * 算出浮层该贴在哪。返回的样式直接给浮层用。
 *
 * @param anchorRef 触发器的 ref（浮层贴着它的下沿或上沿）
 * @param open      浮层是否打开；关闭时清掉坐标，下次打开重新测
 */
export function useAnchoredPopup(
  anchorRef: RefObject<HTMLElement | null>,
  open: boolean,
): CSSProperties {
  const [style, setStyle] = useState<CSSProperties | null>(null);

  const measure = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;

    const rect = anchor.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom - GAP - VIEWPORT_MARGIN;
    const above = rect.top - GAP - VIEWPORT_MARGIN;
    // 下方不够、且上方更宽裕 → 翻上去
    const flip = below < FLIP_THRESHOLD && above > below;
    const room = flip ? above : below;

    setStyle({
      position: "fixed",
      // 触发器贴到屏幕左边时不要把浮层顶出去（窄窗口下会出现）
      left: Math.max(
        VIEWPORT_MARGIN,
        Math.min(rect.left, window.innerWidth - rect.width - VIEWPORT_MARGIN),
      ),
      width: rect.width,
      maxHeight: Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, room)),
      ...(flip
        ? { bottom: window.innerHeight - rect.top + GAP }
        : { top: rect.bottom + GAP }),
    });
  }, [anchorRef]);

  useIsomorphicLayoutEffect(() => {
    if (!open) {
      setStyle(null);
      return;
    }

    measure();

    /*
     * 捕获阶段监听滚动：滚动可能发生在**任意祖先**上（控制台的
     * `<main class="consoleScroll">` 就是滚动容器），冒泡阶段收不到那些事件。
     * 不跟着更新的话，浮层会留在原地、和触发器脱节。
     */
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [open, measure]);

  return style ?? UNMEASURED;
}
