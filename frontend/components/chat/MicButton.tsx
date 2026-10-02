"use client";

/**
 * 语音输入按钮（点击开始 / 再点结束）。
 *
 * 三态：空闲 → 录音中 → 识别中。**识别结果只回填输入框，不自动发送**
 * （理由见 `hooks/perception/useSpeechInput.ts` 的模块文档）。
 *
 * 能力不可用时**整个按钮不渲染**——画一个点了会报错的按钮，
 * 用户会读成「功能坏了」，而真实原因往往只是「后端没装 faster-whisper」。
 */

import type { SpeechInput } from "@/hooks/perception/useSpeechInput";

/** 键盘焦点样式（与其他面板同一套写法，见 ChatPanel） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

export function MicButton({
  speech,
  show = true,
}: {
  speech: SpeechInput;
  /** 用户是否在插件设置里保留了按钮（`asr_ui_enabled`） */
  show?: boolean;
}) {
  /*
   * 录音 / 转写中**必须**显示，即使用户把按钮关掉了：
   * 快捷键不依赖按钮也能开录音，那时他需要一个「正在录」的指示和停止入口——
   * 否则麦克风开着却没有任何可见的关闭方式。
   */
  const visible = (show && speech.available) || speech.recording || speech.transcribing;
  if (!visible) return null;

  const { recording, transcribing, shortcutLabel } = speech;
  const label = transcribing ? "识别中…" : recording ? "结束" : "语音";
  const shortcutHint = shortcutLabel ? `（快捷键 ${shortcutLabel}）` : "";
  const title = transcribing
    ? "正在把录音转成文字…"
    : recording
      ? `点一下结束录音${shortcutHint}（识别结果会填进输入框，不会自动发送）`
      : `用语音说${shortcutHint}（识别结果会填进输入框，不会自动发送）`;

  return (
    <button
      type="button"
      // 识别中禁用：此刻再点会与上一段录音的收尾流程抢状态
      disabled={transcribing}
      onClick={() => (recording ? speech.stop() : void speech.start())}
      title={title}
      aria-label={title}
      aria-pressed={recording}
      className={`btn h-11 shrink-0 ${recording ? "btn-danger" : "btn-secondary"} ${FOCUS_RING}`}
    >
      {transcribing ? (
        <span aria-hidden="true" className="flex items-center gap-1">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-current"
              style={{ animationDelay: `${index * 200}ms` }}
            />
          ))}
        </span>
      ) : recording ? (
        <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm bg-current" />
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
          <path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z" />
          <path d="M5 11a7 7 0 0 0 14 0" />
          <path d="M12 18v3" />
        </svg>
      )}
      {label}
    </button>
  );
}
