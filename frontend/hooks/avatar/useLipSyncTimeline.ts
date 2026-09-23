"use client";

/**
 * 播报口型时间轴：播报时向后端取 viseme 序列，交给渲染器驱动嘴型。
 *
 * 三条约束：
 * 1. **只在播报时请求**——idle / think 不去打扰后端；
 * 2. **令牌防竞态**——分段播报时字幕会连续变化，慢的旧请求不许覆盖新段的口型；
 * 3. **失败静默**——拿不到时间轴只是嘴不动，绝不影响对话、语音与字幕。
 */

import { useEffect, useRef, useState } from "react";

import { postAvatar } from "@/lib/api/client";
import type { VisemeFrame } from "@/lib/api/types";

export function useLipSyncTimeline({
  active,
  text,
  emotion,
  intensity,
}: {
  /** 是否正在播报（`avatar.state === "speak"`） */
  active: boolean;
  /** 当前播报的文本（分段播报时是当前段的字幕） */
  text: string;
  /** 情绪标签（英文），供后端产出与情绪匹配的语气时长 */
  emotion?: string | null;
  /** 情绪强度 0-1 */
  intensity?: number;
}): readonly VisemeFrame[] | null {
  const [timeline, setTimeline] = useState<readonly VisemeFrame[] | null>(null);
  /** 请求令牌：只有最后一次请求的结果允许落地 */
  const tokenRef = useRef(0);
  const trimmed = text.trim();

  useEffect(() => {
    const token = ++tokenRef.current;

    if (!active || !trimmed) {
      setTimeline(null);
      return;
    }

    const controller = new AbortController();
    void postAvatar(
      { text: trimmed, emotion: emotion ?? null, intensity: intensity ?? 0.5 },
      { signal: controller.signal },
    ).then((data) => {
      if (token !== tokenRef.current) return; // 已过期：让位给更新的那一段
      setTimeline(data?.visemes?.length ? data.visemes : null);
    });

    return () => controller.abort();
  }, [active, trimmed, emotion, intensity]);

  return timeline;
}
