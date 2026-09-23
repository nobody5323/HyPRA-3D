/**
 * 口型时间轴的**渲染无关**处理：采样 + viseme → 参数映射。
 *
 * 分工：
 * - 后端 `POST /media/avatar` 产出的是 **viseme 序列**（10 种口型 + 静默）；
 * - 渲染器需要的是「这一刻嘴张多大、什么形状」；
 * - 本模块就是这层翻译，全部是纯函数（可单测，不依赖任何渲染器或 SDK）。
 *
 * 映射值对齐 Live2D 的参数语义（`ParamMouthOpenY` 0 闭 ~ 1 全开、
 * `ParamMouthForm` -1 扁 ~ +1 圆），但**不依赖 Live2D**——
 * 换任何 2D/3D 渲染器都只需要重新解释这两个归一化值。
 */

import type { VisemeFrame } from "@/lib/api/types";

/** 开口度：静默/闭唇音为 0，元音按开口大小递减（A > O > E > I > U） */
export const VISEME_OPENNESS: Readonly<Record<string, number>> = {
  sil: 0,
  A: 1,
  O: 0.66,
  E: 0.6,
  I: 0.4,
  U: 0.3,
  F: 0.22,
  N: 0.2,
  S: 0.15,
  M: 0, // 闭唇音：嘴本来就是闭的
};

/** 嘴形：负值扁（齿音/扁口），正值圆（圆唇） */
export const VISEME_MOUTH_FORM: Readonly<Record<string, number>> = {
  sil: 0,
  A: 0,
  E: -0.2,
  I: -0.6,
  U: 0.8,
  O: 0.9,
  M: 0,
  F: -0.3,
  N: -0.4,
  S: -0.5,
};

/** 静默口型（默认值，也是未知 viseme 的落点） */
export const SILENT_VISEME = "sil";

/** 归一化后的口型形状 */
export interface MouthShape {
  /** 开口度 0-1 */
  openness: number;
  /** 嘴形 -1（扁）~ +1（圆） */
  form: number;
}

/** 闭嘴（停止播报、时间轴为空、播放结束都用它） */
export const CLOSED_MOUTH: MouthShape = { openness: 0, form: 0 };

/**
 * viseme 归一化。
 *
 * 未知值一律按静默处理——**不臆造口型**：宁可不张嘴，也不要随机张合。
 */
export function normalizeViseme(value: unknown): string {
  const key = typeof value === "string" ? value.trim() : "";
  return Object.prototype.hasOwnProperty.call(VISEME_OPENNESS, key) ? key : SILENT_VISEME;
}

/** 时间轴总时长（毫秒）；空时间轴为 0 */
export function timelineDurationMs(timeline: readonly VisemeFrame[]): number {
  let total = 0;
  for (const frame of timeline) {
    if (frame.end_ms > total) total = frame.end_ms;
  }
  return total;
}

/**
 * 按经过时间采样口型。
 *
 * - `elapsedMs` 为负（时钟抖动）或超出时间轴 → 闭嘴；
 * - 线性扫描即可：一帧几十到几百个口型，每帧 O(n) 的开销远小于渲染本身。
 */
export function sampleMouth(timeline: readonly VisemeFrame[], elapsedMs: number): MouthShape {
  if (timeline.length === 0 || elapsedMs < 0) return CLOSED_MOUTH;

  for (const frame of timeline) {
    if (elapsedMs < frame.start_ms || elapsedMs >= frame.end_ms) continue;
    const viseme = normalizeViseme(frame.viseme);
    return { openness: VISEME_OPENNESS[viseme], form: VISEME_MOUTH_FORM[viseme] };
  }

  // 落在帧与帧的空隙里、或已播放完毕 → 闭嘴
  return CLOSED_MOUTH;
}

/**
 * 字符位置 → 播放时间（毫秒）。
 *
 * 用途：服务端 TTS（GPT-SoVITS）是**整段合成**——一次请求就得到整段音频，
 * 但字幕仍要按段推进，所以需要知道「第 N 个字大约在第几毫秒被念到」。
 *
 * 两条策略：
 * - 时间轴每帧对应一个字符时（`POST /media/avatar` 的估算轨道就是如此）→ **精确**取该帧起点；
 * - 帧数与文本长度不符时（例如带字级时间戳的来源会合并标点）→ 按长度比例估算。
 */
export function charOffsetToMs(
  timeline: readonly VisemeFrame[] | null | undefined,
  offset: number,
  textLength: number,
  durationMs: number,
): number {
  if (textLength <= 0) return 0;
  const clamped = Math.max(0, Math.min(Math.trunc(offset), textLength));

  if (timeline && timeline.length === textLength && clamped < timeline.length) {
    return timeline[clamped].start_ms;
  }
  if (durationMs > 0) return Math.round((durationMs * clamped) / textLength);
  return 0;
}
