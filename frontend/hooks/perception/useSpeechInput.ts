"use client";

/**
 * 语音输入：录音 → 后端转写 → **回填输入框**（不自动发送）。
 *
 * 设计见 `docs/proactive-multimodal.md` §4.3。三条刻意的取舍：
 *
 * 1. **不自动发送**。ASR 有误识别，自动发送会让「识别错一个字」直接变成
 *    「对话跑偏」。把文本交回输入框让用户看一眼，是零成本的保险——
 *    而且这一步同时也让「录音识别错了」和「模型答歪了」在用户眼里可区分。
 * 2. **不自动开始录音**。麦克风权限弹窗是**用户可见的系统行为**，
 *    必须在用户点击之后发生，不能在挂载时偷偷请求。
 * 3. **拿不到能力就不渲染入口**。`supported` 为 false 时调用方不该画麦克风按钮——
 *    画一个点了会报错的按钮，用户读成「功能坏了」。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, transcribeAudio } from "@/lib/api/client";
import {
  formatShortcut,
  isTypingTarget,
  matchesShortcut,
  stealsTyping,
} from "@/lib/chat/shortcut";

/** 录音格式候选（按优先级）。浏览器支持哪种就用哪种。 */
const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

/** 单次录音的时长上限（毫秒）：语音输入是「说一句话」，不是录音文件。 */
const MAX_RECORDING_MS = 60_000;

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const candidate of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return undefined;
}

/** 浏览器是否具备录音 + 转写的完整链路。 */
export function speechInputSupported(): boolean {
  if (typeof navigator === "undefined") return false;
  return (
    typeof MediaRecorder !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function"
  );
}

export interface SpeechInputOptions {
  /** 转写成功后的回调（调用方负责填进输入框） */
  onText: (text: string) => void;
  /** 是否启用（后端 `asr_available` 为 false 时不启用，避免白占麦克风） */
  enabled?: boolean;
  /** 语言（留空用后端配置） */
  language?: string;
  /**
   * 快捷键（形如 `Ctrl+Shift+M`；空 = 不启用）。按一下开始、再按一下结束。
   *
   * 由**用户在插件设置里自己填**（`ASR_SHORTCUT`）——因为「哪个键顺手」
   * 因人而异，而且不同人的软件环境里被占用的键也不同。
   * 只在窗口获得焦点时生效：不注册系统级全局热键（那会与其它软件抢键）。
   */
  shortcut?: string;
}

export interface SpeechInput {
  /** 浏览器与后端都具备语音输入能力 */
  available: boolean;
  recording: boolean;
  transcribing: boolean;
  error: string | null;
  /** 快捷键的展示文案（空 = 未配置），供按钮提示语使用 */
  shortcutLabel: string;
  start: () => Promise<void>;
  /** 结束录音并开始转写 */
  stop: () => void;
  /** 放弃本次录音（不转写、不上传） */
  cancel: () => void;
  clearError: () => void;
}

export function useSpeechInput(options: SpeechInputOptions): SpeechInput {
  const { onText, enabled = true, language, shortcut = "" } = options;

  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  /** 本次录音是否作废（用户点了取消） */
  const discardedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 组件卸载后不再写状态 */
  const mountedRef = useRef(true);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // 卸载时必须释放麦克风：否则浏览器标签页会一直显示「正在录音」的指示
      discardedRef.current = true;
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      releaseStream();
    };
  }, [releaseStream]);

  const transcribe = useCallback(async (blob: Blob) => {
    if (blob.size === 0) {
      if (mountedRef.current) setError("没有录到声音，请靠近麦克风再试一次。");
      return;
    }
    if (mountedRef.current) {
      setTranscribing(true);
      setError(null);
    }
    try {
      const file = new File([blob], "speech.webm", { type: blob.type || "audio/webm" });
      const result = await transcribeAudio(file, { language });
      if (!mountedRef.current) return;
      if (!result.has_text) {
        // 转写成功但没内容：与「失败」区分开——用户只是没说话
        setError("没有识别到语音内容，请再说一次。");
        return;
      }
      onTextRef.current(result.text);
    } catch (err) {
      if (!mountedRef.current) return;
      // 后端的 detail 里带着可照做的信息（模型放哪 / 依赖没装），原样展示
      setError(
        err instanceof ApiError
          ? err.message
          : "语音识别失败，请检查后端是否已启动。",
      );
    } finally {
      if (mountedRef.current) setTranscribing(false);
    }
  }, [language]);

  const start = useCallback(async () => {
    if (!speechInputSupported() || !enabled || recording) return;
    setError(null);
    discardedRef.current = false;

    let stream: MediaStream;
    try {
      // 权限弹窗只会在用户点击后出现（见模块文档第 2 条）
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("无法访问麦克风。请检查浏览器的麦克风权限设置。");
      return;
    }
    if (!mountedRef.current) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    streamRef.current = stream;
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
      chunksRef.current = [];
      releaseStream();
      if (mountedRef.current) setRecording(false);
      if (!discardedRef.current) void transcribe(blob);
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);

    // 上限保护：忘记点停止时自动收尾（否则一直占着麦克风）
    timerRef.current = setTimeout(() => {
      if (recorder.state === "recording") recorder.stop();
    }, MAX_RECORDING_MS);
  }, [enabled, recording, releaseStream, transcribe]);

  const stop = useCallback(() => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, []);

  const cancel = useCallback(() => {
    discardedRef.current = true;
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
      return;
    }
    releaseStream();
    if (mountedRef.current) setRecording(false);
  }, [releaseStream]);

  const clearError = useCallback(() => setError(null), []);

  /**
   * 快捷键：按一下开始、再按一下结束（等价于点按钮）。
   *
   * 两条防误触规则：
   * - `event.repeat` 直接忽略：长按会连发 keydown，不拦的话会「开了又关」；
   * - 不带 Ctrl/Alt/Meta 的快捷键（如 `M`）在输入框里**让路**——
   *   否则用户打字打到 M 就开始录音（见 `lib/chat/shortcut.ts` 的 `stealsTyping`）。
   */
  useEffect(() => {
    if (!enabled || !shortcut) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.repeat) return;
      if (!matchesShortcut(event, shortcut)) return;
      if (stealsTyping(shortcut) && isTypingTarget(event.target)) return;

      event.preventDefault();
      // 转写中不响应：此刻再开一段录音会与上一段的收尾流程抢状态
      if (transcribing) return;
      if (recording) stop();
      else void start();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [enabled, recording, shortcut, start, stop, transcribing]);

  return {
    available: enabled && speechInputSupported(),
    recording,
    transcribing,
    error,
    shortcutLabel: formatShortcut(shortcut),
    start,
    stop,
    cancel,
    clearError,
  };
}
