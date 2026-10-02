"use client";

/**
 * 语音引擎与音色偏好（含后端探测）。
 *
 * 把三件容易互相脱节的事收在一处：
 * 1. **探测**：`GET /media/tts/voices` 告诉前端后端有没有服务端 TTS；
 * 2. **偏好**：用户可手动指定引擎与音色（localStorage，跨刷新保持）；
 * 3. **失效音色回落**：音色表换了之后，保存的 id 可能已不存在——
 *    必须自动回落到后端默认音色，否则界面会留一个「选了却没有任何变化」的死选项。
 *
 * 降级原则：探测失败（后端未就绪 / 网络异常）只会让 `status` 为 null，
 * 由调用方按「没接入 TTS 就静默」处理，**绝不抛错、绝不阻断页面**。
 */

import { useCallback, useEffect, useState } from "react";

import { fetchTtsVoices } from "@/lib/api/client";
import {
  getInitialTtsEnginePreference,
  getInitialTtsVoice,
  readTtsEnginePreference,
  readTtsVoice,
  saveTtsEnginePreference,
  saveTtsVoice,
  subscribeTtsEnginePreference,
  subscribeTtsVoice,
  type TtsEnginePreference,
} from "@/lib/avatar/avatar-config";
import type { TtsVoicesStatus } from "@/lib/api/types";

export interface TtsPreferences {
  /** 后端语音引擎状态（null = 尚未探测到 / 探测失败） */
  status: TtsVoicesStatus | null;
  /** 后端是否真的能出音频（探测为「有服务端 TTS」且「已配好」） */
  serverTtsAvailable: boolean;
  engine: TtsEnginePreference;
  setEngine: (value: TtsEnginePreference) => void;
  /** 选中的音色 id（空串 = 后端默认音色） */
  voice: string;
  setVoice: (voice: string) => void;
}

export function useTtsPreferences({ enabled }: { enabled: boolean }): TtsPreferences {
  const [status, setStatus] = useState<TtsVoicesStatus | null>(null);
  // 首屏用「不读 localStorage」的安全值，挂载后再读真实偏好（否则 SSR/CSR 不一致）
  const [engine, setEngineState] = useState<TtsEnginePreference>(getInitialTtsEnginePreference);
  const [voice, setVoiceState] = useState<string>(getInitialTtsVoice);

  useEffect(() => {
    const syncEngine = () => setEngineState(readTtsEnginePreference());
    const syncVoice = () => setVoiceState(readTtsVoice());
    syncEngine();
    syncVoice();
    const unsubscribeEngine = subscribeTtsEnginePreference(syncEngine);
    const unsubscribeVoice = subscribeTtsVoice(syncVoice);
    return () => {
      unsubscribeEngine();
      unsubscribeVoice();
    };
  }, []);

  // 探测：后端就绪后取一次（探测失败只记 null，由上层按「静默」处理）
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void fetchTtsVoices().then((result) => {
      if (!cancelled) setStatus(result);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  /**
   * 音色失效回落：音色表改了/换了机器后，本地记的音色 id 可能已不存在。
   * 不处理的话用户会看到「界面上选着 A，实际用的是默认音色」——最难查的一类不一致。
   */
  useEffect(() => {
    if (!status || !voice) return;
    if (status.voices.some((item) => item.id === voice)) return;
    setVoiceState("");
    saveTtsVoice("");
  }, [status, voice]);

  const setEngine = useCallback((value: TtsEnginePreference) => {
    saveTtsEnginePreference(value);
    setEngineState(value);
  }, []);

  const setVoice = useCallback((value: string) => {
    const next = String(value ?? "").trim();
    saveTtsVoice(next);
    setVoiceState(next);
  }, []);

  return {
    status,
    serverTtsAvailable: Boolean(status?.server_tts && status.configured),
    engine,
    setEngine,
    voice,
    setVoice,
  };
}
