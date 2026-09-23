import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { Live2DStage } from "@/components/avatar/Live2DStage";
import type { AvatarModelsState } from "@/hooks/avatar/useAvatarModels";
import { resolveAvatarModelSource } from "@/lib/avatar/avatar-model-source";
import { PORTRAIT_LABELS } from "@/lib/avatar/portrait-assets";
import {
  AVATAR_STATE_LABELS,
  type AvatarLayout,
  type AvatarState,
  type EmotionInfo,
} from "@/lib/api/types";

/**
 * 模型调试台。
 *
 * 存在的理由：Live2D 模型的画布比例差别很大（半身 / 全身 / 偏移），同一套默认构图
 * 必然有的模型显示不全；而桌宠窗里「模型与底部按钮分离」正是这个原因——
 * 拖动桌宠只能移动整个窗口，改不了模型在窗口里的构图。
 *
 * 这里把构图变成可调、可视、可保存的：调完存进模型元数据（后端），
 * **Web 端与桌面端读到的是同一份**，所以调一次两边都对。
 *
 * 调试的是**草稿**：改动实时作用于预览，但不落盘，直到点「保存」。
 * 这样用户可以先试，不满意直接放弃。
 */

/** 缩放区间与后端 `sanitize_layout` 保持一致（越界会被后端拒掉） */
const SCALE_MIN = 0.1;
const SCALE_MAX = 5;
const SCALE_STEP = 0.05;
/** 位移上限，同样与后端一致 */
const OFFSET_LIMIT = 2000;

/** 锚点候选：CSS transform-origin 的常见写法（调试时按模型比例选一个） */
const ANCHORS = ["bottom center", "center", "top center", "center bottom", "50% 100%"] as const;

/** 构造一个「只为预览用」的情绪对象（控制台里没有真实对话） */
function previewEmotion(label: string): EmotionInfo {
  return {
    label,
    label_zh: PORTRAIT_LABELS[label as keyof typeof PORTRAIT_LABELS] ?? label,
    intensity: 0.7,
    confidence: 1,
    facial_expression: label,
    source: "control-panel",
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function ModelDebugPanel({ models }: { models: AvatarModelsState }) {
  const selected = models.selected;
  /** 正在编辑的构图草稿（null = 未校准） */
  const [draft, setDraft] = useState<AvatarLayout | null>(null);
  const [mood, setMood] = useState<string>("neutral");
  const [motion, setMotion] = useState<AvatarState>("idle");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [renderDetail, setRenderDetail] = useState("");

  // 换模型（或后端刷新出新校准）→ 草稿以已存值为起点
  const savedLayout = selected?.layout ?? null;
  useEffect(() => {
    setDraft(savedLayout);
    setNotice(null);
  }, [selected?.id, savedLayout]);

  /**
   * 预览用的模型来源。
   *
   * 只依赖 `selected`：草稿构图单独传下去（`previewLayout`）。
   * 如果把 `draft` 也拌进来重建 source，拖动时每次移动都会换一个 source 对象，
   * 而渲染器靠引用判等——那会让它在拖动过程中反复重建。
   */
  const previewSource = useMemo(() => resolveAvatarModelSource(selected), [selected]);

  /**
   * 能否调构图。
   *
   * `builtin` **也算 Live2D**（它就是内置的那套 Live2D 模型），只是它的构图写在
   * `model-assets.ts` 的常量里、存不进后端——所以能预览、不能保存。
   * 只有 `images`（静态立绘）才是真的不适用构图校准。
   */
  const isLive2D = previewSource.kind !== "images";
  const previewLayout = isLive2D ? draft : null;

  // ---------------- 拖动调位置 ----------------

  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (!isLive2D || event.button !== 0) {
      return;
    }

    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      ox: draft?.offsetX ?? 0,
      oy: draft?.offsetY ?? 0,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const start = dragRef.current;

    if (!start) {
      return;
    }

    setDraft((prev) => ({
      ...(prev ?? {}),
      offsetX: clamp(Math.round(start.ox + event.clientX - start.x), -OFFSET_LIMIT, OFFSET_LIMIT),
      offsetY: clamp(Math.round(start.oy + event.clientY - start.y), -OFFSET_LIMIT, OFFSET_LIMIT),
    }));
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>): void => {
    dragRef.current = null;

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  // ---------------- 保存 / 重置 ----------------

  const persist = async (next: AvatarLayout | null, okText: string): Promise<void> => {
    if (!selected) {
      return;
    }

    setBusy(true);
    setNotice(null);

    try {
      await models.updateLayout(selected.id, next);
      setNotice({ tone: "ok", text: okText });
    } catch (error: unknown) {
      setNotice({ tone: "warn", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  };

  const dirty = JSON.stringify(draft ?? null) !== JSON.stringify(savedLayout);

  const scale = draft?.scale ?? 1;
  const offsetX = draft?.offsetX ?? 0;
  const offsetY = draft?.offsetY ?? 0;

  return (
    <div className="flex flex-col gap-4">
      {/* 模型选择：内置 + 后端清单 */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs text-ink-soft" htmlFor="debug-model">
          调试对象
        </label>
        <select
          id="debug-model"
          value={models.selectedId}
          onChange={(event) => models.select(event.target.value)}
          className="rounded-xl border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <option value="">项目内置模型</option>
          {models.models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}（{model.kind === "live2d" ? "Live2D" : "静态立绘"}）
            </option>
          ))}
        </select>
        {models.loading ? <span className="text-[11px] text-ink-soft">正在读取模型清单…</span> : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        {/* 预览区：拖动 = 调位移 */}
        <div>
          <div
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            className={`relative h-[420px] overflow-hidden rounded-2xl border border-line bg-surface-inset ${
              isLive2D ? "cursor-grab active:cursor-grabbing" : ""
            }`}
          >
            <Live2DStage
              emotion={previewEmotion(mood)}
              motion={motion}
              modelUrl={previewSource.kind === "live2d" ? previewSource.modelUrl : null}
              modelLayout={previewLayout}
              onStageChange={(_stage, detail) => setRenderDetail(detail)}
              onError={(error) => setRenderDetail(error.message)}
            />

            {/* 画布参考线：帮助判断模型有没有居中 / 超出边界 */}
            <div className="pointer-events-none absolute inset-x-0 top-1/2 h-px bg-line/60" />
            <div className="pointer-events-none absolute inset-y-0 left-1/2 w-px bg-line/60" />

            <p className="pointer-events-none absolute bottom-2 left-2 rounded-lg bg-surface-panel/85 px-2 py-1 text-[10px] text-ink-soft">
              {isLive2D ? "按住拖动可调整模型位置" : "静态立绘模型不支持构图拖动"}
            </p>
          </div>

          <ul className="mt-2 flex flex-wrap items-end justify-between gap-2 text-[11px] text-ink-soft">
            <li>缩放 {scale.toFixed(2)}×　位移 {offsetX} / {offsetY} px</li>
            <li>{renderDetail}</li>
          </ul>
        </div>

        {/* 控件区 */}
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-ink-soft">
            <span>
              缩放 <strong className="text-ink">{scale.toFixed(2)}×</strong>
            </span>
            <input
              type="range"
              min={SCALE_MIN}
              max={SCALE_MAX}
              step={SCALE_STEP}
              value={scale}
              disabled={!isLive2D}
              onChange={(event) =>
                setDraft((prev) => ({ ...(prev ?? {}), scale: Number(event.target.value) }))
              }
              className="accent-accent disabled:opacity-40"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-ink-soft">
            <span>水平位移（px）</span>
            <input
              type="number"
              value={offsetX}
              disabled={!isLive2D}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...(prev ?? {}),
                  offsetX: clamp(Math.round(Number(event.target.value) || 0), -OFFSET_LIMIT, OFFSET_LIMIT),
                }))
              }
              className="rounded-xl border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-ink-soft">
            <span>垂直位移（px）</span>
            <input
              type="number"
              value={offsetY}
              disabled={!isLive2D}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...(prev ?? {}),
                  offsetY: clamp(Math.round(Number(event.target.value) || 0), -OFFSET_LIMIT, OFFSET_LIMIT),
                }))
              }
              className="rounded-xl border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-ink-soft">
            <span>锚点（CSS transform-origin）</span>
            <select
              value={draft?.anchor ?? ""}
              disabled={!isLive2D}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...(prev ?? {}),
                  anchor: event.target.value || undefined,
                }))
              }
              className="rounded-xl border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40"
            >
              <option value="">默认（bottom center）</option>
              {ANCHORS.map((anchor) => (
                <option key={anchor} value={anchor}>
                  {anchor}
                </option>
              ))}
            </select>
          </label>

          <div className="flex flex-col gap-1 text-xs text-ink-soft">
            <span>试播情绪</span>
            <div className="flex flex-wrap gap-1">
              {Object.entries(PORTRAIT_LABELS).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setMood(id)}
                  aria-pressed={mood === id}
                  className={`rounded-full px-2 py-1 text-[11px] ring-1 transition-colors ${
                    mood === id
                      ? "bg-accent-soft text-accent-text ring-accent/30"
                      : "bg-surface-raised text-ink-muted ring-line hover:bg-surface-hover"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1 text-xs text-ink-soft">
            <span>试播动作</span>
            <div className="flex flex-wrap gap-1">
              {(Object.keys(AVATAR_STATE_LABELS) as AvatarState[]).map((state) => (
                <button
                  key={state}
                  type="button"
                  onClick={() => setMotion(state)}
                  aria-pressed={motion === state}
                  className={`rounded-full px-2 py-1 text-[11px] ring-1 transition-colors ${
                    motion === state
                      ? "bg-accent-soft text-accent-text ring-accent/30"
                      : "bg-surface-raised text-ink-muted ring-line hover:bg-surface-hover"
                  }`}
                >
                  {AVATAR_STATE_LABELS[state]}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-1 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || !selected || !dirty}
              onClick={() => void persist(draft, "构图已保存；Web 端与桌宠窗都会按它渲染")}
              className="rounded-full bg-accent-soft px-4 py-1.5 text-xs text-accent-text ring-1 ring-accent/30 disabled:opacity-40"
            >
              保存构图
            </button>
            <button
              type="button"
              disabled={busy || !dirty}
              onClick={() => setDraft(savedLayout)}
              className="rounded-full bg-surface-raised px-4 py-1.5 text-xs text-ink-muted ring-1 ring-line disabled:opacity-40"
            >
              放弃改动
            </button>
            <button
              type="button"
              disabled={busy || !selected || savedLayout === null}
              onClick={() => void persist(null, "已恢复默认构图")}
              className="rounded-full bg-surface-raised px-4 py-1.5 text-xs text-ink-muted ring-1 ring-line disabled:opacity-40"
            >
              恢复默认
            </button>
          </div>

          {!selected ? (
            <p className="rounded-xl bg-surface-inset px-3 py-2 text-[11px] leading-relaxed text-ink-soft">
              当前用的是<strong className="text-ink">项目内置模型</strong>
              ：可以拖动预览效果，但它的构图写在内置清单里（
              <code className="rounded bg-surface-hover px-1">model-assets.ts</code>
              ），存不进后端。要保存校准，请先在「模型库」上传模型并在上面切换过去。
            </p>
          ) : null}

          {notice ? (
            <p
              role="status"
              className={`rounded-xl px-3 py-2 text-[11px] leading-relaxed ${
                notice.tone === "ok"
                  ? "bg-success-soft text-success-text"
                  : "bg-danger-soft text-danger-text"
              }`}
            >
              {notice.text}
            </p>
          ) : null}

          {models.error ? (
            <p className="rounded-xl bg-danger-soft px-3 py-2 text-[11px] text-danger-text">
              {models.error}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
