import type { SpeechInput } from "@/hooks/perception/useSpeechInput";

/**
 * 桌宠窗的语音入口（图标按钮）。
 *
 * **为什么不用 Web 端的 `MicButton`**：桌宠入口（`pet/main.tsx`）只加载 `pet.css`，
 * 没有接 Tailwind；而 `@/components/*` 那些组件全靠 Tailwind 类写样式——
 * 引进来不会报错，但会渲染成没有样式的裸元素。这条边界由
 * `tests/pet-renderer-boundary.test.ts` 钉着（样式相关的复用必须走 `.pet*` 体系）。
 *
 * 只画图标不画文字：面板只有 190px 宽，带文字的按钮会把输入框挤没。
 * 语义由 `title` 与 `aria-label` 承担。
 *
 * 行为与 Web 端**完全一致**（都用 `useSpeechInput`）：识别结果只回填输入框，
 * 由用户确认后再发——ASR 有误识别，自动发送会让「识别错一个字」直接变成「对话跑偏」。
 */
export function PetMicButton({
  speech,
  show = true,
}: {
  speech: SpeechInput;
  /** 用户是否在插件设置里保留了按钮（`asr_ui_enabled`） */
  show?: boolean;
}) {
  /*
   * 录音 / 转写中**必须**显示，即使用户把按钮关掉了：
   * 快捷键不依赖按钮也能开录音，那时他需要一个停止入口——
   * 否则麦克风开着却没有任何可见的关闭方式。
   */
  const visible = (show && speech.available) || speech.recording || speech.transcribing;
  if (!visible) return null;

  const { recording, transcribing, shortcutLabel } = speech;
  const shortcutHint = shortcutLabel ? `（快捷键 ${shortcutLabel}）` : "";
  const title = transcribing
    ? "正在把录音转成文字…"
    : recording
      ? `点一下结束录音${shortcutHint}（识别结果会填进输入框，不会自动发送）`
      : `用语音说${shortcutHint}（识别结果会填进输入框，不会自动发送）`;

  return (
    <button
      type="button"
      className={`petMic${recording ? " petMicRecording" : ""}`}
      disabled={transcribing}
      onClick={() => (recording ? speech.stop() : void speech.start())}
      title={title}
      aria-label={title}
      aria-pressed={recording}
    >
      {transcribing ? (
        <span className="petMicDots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      ) : recording ? (
        <span className="petMicStop" aria-hidden="true" />
      ) : (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="14"
          height="14"
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
    </button>
  );
}
