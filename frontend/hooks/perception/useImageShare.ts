"use client";

/**
 * 分享图片：选图 → 后端理解 → **回填输入框**（不自动发送）。
 *
 * 设计见 `docs/proactive-multimodal.md` §4.4。三条与 ASR 同源的口径：
 *
 * 1. **只由用户动作触发**。没有定时器、没有后台采集——结构上就不存在
 *    「关掉开关还在偷偷采」这种可能（摄像头与连续屏幕感知已明确否决）。
 * 2. **不自动发送**。图片理解的结果只是一条**感知事实**；
 *    要让它影响回复，仍需走一次 `/chat`（由用户按下发送）。
 *    这样「看图」与「说话」是两次调用、两个模型，视觉模型缺失时
 *    整个对话链路照常工作。
 * 3. **能力不可用就不渲染入口**（`available` 为 false 时调用方不画按钮）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, describeImage } from "@/lib/api/client";

/** 支持的类型（与后端的 data URL 内联口径一致） */
const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

/** 允许的最大图片大小（MB）。与后端 `VISION_MAX_IMAGE_MB` 的默认值对齐 */
const MAX_IMAGE_MB = 8;

export interface ImageShareOptions {
  /** 理解完成后的回调（调用方负责把提示语填进输入框） */
  onReady: (description: string) => void;
  /** 是否启用（后端 `vision_available` 为 false 时不启用） */
  enabled?: boolean;
}

export interface ImageShare {
  /** 浏览器与后端都具备图片理解能力 */
  available: boolean;
  analyzing: boolean;
  error: string | null;
  /** 打开系统文件选择器（由调用方在按钮点击时调用） */
  pick: () => void;
  clearError: () => void;
}

/** 浏览器是否能选图（本能力不依赖任何特殊 API，只要能读文件） */
export function imageShareSupported(): boolean {
  return typeof document !== "undefined" && typeof FileReader !== "undefined";
}

export function useImageShare(options: ImageShareOptions): ImageShare {
  const { onReady, enabled = true } = options;
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement | null>(null);
  const mountedRef = useRef(true);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const analyze = useCallback(async (file: File) => {
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError("只支持 PNG / JPG / WEBP / GIF 图片。");
      return;
    }
    if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
      setError(`图片太大了（上限 ${MAX_IMAGE_MB} MB）。`);
      return;
    }

    setAnalyzing(true);
    setError(null);
    try {
      const result = await describeImage(file);
      if (!mountedRef.current) return;
      if (!result.description.trim()) {
        // 理解成功但没看出什么：与「失败」区分开——不是配置问题
        setError("没能从这张图里看出内容，换一张试试？");
        return;
      }
      onReadyRef.current(result.description);
    } catch (err) {
      if (!mountedRef.current) return;
      // 后端的 detail 里带着可照做的信息（怎么配 Key / 哪个模型），原样展示
      setError(
        err instanceof ApiError ? err.message : "图片理解失败，请检查后端是否已启动。",
      );
    } finally {
      if (mountedRef.current) setAnalyzing(false);
    }
  }, []);

  // 隐藏的文件选择器：**必须挂在 DOM 上**才能触发 click（离屏 input 在部分浏览器里不弹窗）
  useEffect(() => {
    if (!enabled || !imageShareSupported()) return;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ACCEPTED_TYPES.join(",");
    input.style.display = "none";
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      // 清空 value：否则连选同一张图不会触发 change（第二次选它没反应）
      input.value = "";
      if (file) void analyze(file);
    });
    document.body.appendChild(input);
    inputRef.current = input;
    return () => {
      inputRef.current = null;
      input.remove();
    };
  }, [analyze, enabled]);

  const pick = useCallback(() => {
    if (!enabled || analyzing) return;
    setError(null);
    inputRef.current?.click();
  }, [analyzing, enabled]);

  const clearError = useCallback(() => setError(null), []);

  return {
    available: enabled && imageShareSupported(),
    analyzing,
    error,
    pick,
    clearError,
  };
}
