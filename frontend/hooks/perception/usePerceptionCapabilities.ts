"use client";

/**
 * 后端具备哪些感知能力、以及**这些能力该怎么出现在界面上**。
 *
 * 抽成独立 hook 而不是让每个界面自己探测：三处界面（Web / 桌宠窗 / 控制台）
 * 都要用它决定「画不画麦克风、画不画发图、要不要绑快捷键」，
 * 各写一遍的话，后端新增一种感知能力就要改三个地方，
 * 漏一个的表现是「这个界面能语音、那个界面不能」——而用户只会觉得「坏了」。
 *
 * **探测失败 = 全部不可用**（`getPerceptionStatus` 拿不到就回 null）：
 * 画一个点了会报错的按钮，用户会读成「功能坏了」，
 * 而真实原因往往只是「没装 faster-whisper」或「没配 VLM 的 Key」。
 *
 * ## 「能力」与「入口」是两个开关
 *
 * `asr_ui_enabled` / `vision_ui_enabled` 来自插件设置，控制的是**渲染与否**，
 * 不是能力本身：关掉后接口照常可用（快捷键仍能录音）。
 * 分开的意义是让「用快捷键说话、不想看见按钮」这种用法成立。
 */

import { useEffect, useState } from "react";

import { getPerceptionStatus } from "@/lib/api/client";

export interface PerceptionCapabilities {
  /** 语音识别可用（后端 + 浏览器都具备） */
  asr: boolean;
  /** 图片理解可用 */
  vision: boolean;
  /** 桌面情景是否启用（桌宠端据此决定要不要上报） */
  desktop: boolean;
  /** 是否渲染语音按钮（`asr && asr_ui_enabled`） */
  showMic: boolean;
  /** 是否渲染「图片」按钮（`vision && vision_ui_enabled`） */
  showImage: boolean;
  /** 语音快捷键（原样透传；解析与匹配见 `lib/chat/shortcut.ts`） */
  asrShortcut: string;
}

const NONE: PerceptionCapabilities = {
  asr: false,
  vision: false,
  desktop: false,
  showMic: false,
  showImage: false,
  asrShortcut: "",
};

export function usePerceptionCapabilities(): PerceptionCapabilities {
  const [capabilities, setCapabilities] = useState<PerceptionCapabilities>(NONE);

  useEffect(() => {
    let alive = true;
    void getPerceptionStatus().then((status) => {
      if (!alive) return;
      if (!status) {
        setCapabilities(NONE);
        return;
      }
      const asr = Boolean(status.asr_available);
      const vision = Boolean(status.vision_available);
      setCapabilities({
        asr,
        vision,
        desktop: Boolean(status.desktop_enabled),
        // `?? true` 而不是 `?? false`：老后端没有这两个字段时按「显示」处理，
        // 与它们的默认值一致——用 false 会让升级前后行为悄悄变掉
        showMic: asr && (status.asr_ui_enabled ?? true),
        showImage: vision && (status.vision_ui_enabled ?? true),
        asrShortcut: status.asr_shortcut ?? "",
      });
    });
    return () => {
      alive = false;
    };
  }, []);

  return capabilities;
}
