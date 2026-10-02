"use client";

/**
 * 「它还能这样用」的一次性提示（可关掉，记住不再显示）。
 *
 * ## 为什么需要它
 *
 * 语音与发图都是**入口可见、能力不可见**的功能：按钮就摆在那里，
 * 但用户不知道「点了会怎样」——尤其不知道「识别结果不会自动发送」，
 * 于是不敢点。这条提示回答的正是那个疑虑。
 *
 * 只显示一次（关掉后记在 localStorage）：它是**上手引导**，
 * 不是常驻说明。常驻的说明会被无视，而且会一直占着输入区上方的位置。
 *
 * 只在**确实有可用能力**时渲染：没有能力却提示「你可以发图」，
 * 用户点不到按钮，只会更困惑。
 */

import { useEffect, useState } from "react";

import { dismissHint, isHintDismissed } from "@/lib/chat/session-store";

export interface CapabilityHintProps {
  showMic: boolean;
  showImage: boolean;
  /** 已格式化好的快捷键文案（空 = 未配置） */
  shortcutLabel: string;
  /** 记住「已关掉」用的键。同一处界面固定传一个值 */
  hintId: string;
}

/** 按可用能力拼出提示文案（纯函数，便于单测） */
export function capabilityHintText(
  showMic: boolean,
  showImage: boolean,
  shortcutLabel: string,
): string {
  if (!showMic && !showImage) return "";

  if (showMic && showImage) {
    const via = shortcutLabel ? `，或按 ${shortcutLabel} 直接开口` : "";
    return `它还能「看」和「听」：点「图片」发一张给它看，点「语音」说话${via}。识别结果只会填进输入框，不会自动发送。`;
  }
  if (showMic) {
    const via = shortcutLabel ? `，或按 ${shortcutLabel} 直接开口` : "";
    return `点「语音」说话${via}。识别结果只会填进输入框，不会自动发送——你看一眼再决定要不要发。`;
  }
  return "点「图片」发一张给它看。图片不会存到后端，看完就丢。";
}

export function CapabilityHint({
  showMic,
  showImage,
  shortcutLabel,
  hintId,
}: CapabilityHintProps) {
  const [dismissed, setDismissed] = useState(true); // 首帧不显示，避免水合后再消失

  useEffect(() => {
    setDismissed(isHintDismissed(hintId));
  }, [hintId]);

  const text = capabilityHintText(showMic, showImage, shortcutLabel);
  if (dismissed || !text) return null;

  return (
    <p className="notice notice-info mb-2 flex flex-wrap items-start gap-2 text-xs">
      <span className="break-words">{text}</span>
      <button
        type="button"
        onClick={() => {
          dismissHint(hintId);
          setDismissed(true);
        }}
        className="btn btn-sm btn-quiet shrink-0"
      >
        知道了
      </button>
    </p>
  );
}
