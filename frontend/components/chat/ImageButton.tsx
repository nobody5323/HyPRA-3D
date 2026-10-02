"use client";

/**
 * 分享图片按钮（把图给角色看）。
 *
 * 理解结果**只回填输入框提示语**，由用户确认后再发——
 * 与语音输入同一套口径（理由见 `hooks/perception/useImageShare.ts`）。
 *
 * 能力不可用时**整个按钮不渲染**：画一个点了会报错的按钮，
 * 用户会读成「功能坏了」，而真实原因往往只是「没配 VLM 的 Key」。
 */

import type { ImageShare } from "@/hooks/perception/useImageShare";

/** 键盘焦点样式（与其他面板同一套写法，见 ChatPanel） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

export function ImageButton({
  image,
  show = true,
}: {
  image: ImageShare;
  /** 用户是否在插件设置里保留了按钮（`vision_ui_enabled`） */
  show?: boolean;
}) {
  // 分析中即使按钮被关掉也要显示：那是「正在看图」的唯一指示
  if (!(show && image.available) && !image.analyzing) return null;

  const title = image.analyzing
    ? "正在看这张图…"
    : "发一张图给它看（图片不会存到后端，看完就丢）";

  return (
    <button
      type="button"
      disabled={image.analyzing}
      onClick={image.pick}
      title={title}
      aria-label={title}
      className={`btn btn-secondary h-11 shrink-0 ${FOCUS_RING}`}
    >
      {image.analyzing ? (
        <span aria-hidden="true" className="flex items-center gap-1">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-current"
              style={{ animationDelay: `${index * 200}ms` }}
            />
          ))}
        </span>
      ) : (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-4 w-4"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <circle cx="9" cy="10" r="1.5" />
          <path d="M4 17l4.5-4.5 3 3L15 12l5 5" />
        </svg>
      )}
      {image.analyzing ? "看图…" : "图片"}
    </button>
  );
}
