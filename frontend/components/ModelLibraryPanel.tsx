"use client";

/**
 * 模型库面板：上传 / 选用 / 删除自己的数字人模型，并为静态立绘逐个指定情绪。
 *
 * 两类模型的界面差异（必须讲清楚，否则用户会问"为什么 Live2D 没有情绪指定"）：
 * - **Live2D**：表情来自模型自带的 exp3.json，`情绪 → 表情名`属于工程配置
 *   （`lib/live2d/presets.json`），所以这里只需「使用 / 删除」；
 * - **静态立绘**：后端**不猜**哪张图是什么情绪（只有用户自己知道），
 *   所以选中后要逐个指定；未指定的情绪回落到内置占位立绘。
 */

import { useRef, useState } from "react";

import type { AvatarModelsState } from "@/hooks/useAvatarModels";
import { PORTRAIT_IDS } from "@/lib/avatar-renderer";
import { PORTRAIT_LABELS } from "@/lib/portrait-assets";
import type { AvatarModelInfo, AvatarModelKind } from "@/lib/types";

const KIND_LABEL: Record<AvatarModelKind, string> = {
  live2d: "Live2D",
  images: "静态立绘",
};

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

/** 模型的一句话描述（表情数 / 图片数） */
function describeModel(model: AvatarModelInfo): string {
  if (model.kind === "live2d") {
    return model.expressions.length > 0
      ? `${model.expressions.length} 个表情`
      : "未发现表情（模型包里没有 exp3.json）";
  }
  const mapped = Object.keys(model.expressionMap).length;
  return `${model.images.length} 张图片 · 已指定 ${mapped}/${PORTRAIT_IDS.length} 个情绪`;
}

/** 静态立绘的情绪 → 图片 映射编辑器（逐个指定） */
function EmotionMapping({
  model,
  onChange,
}: {
  model: AvatarModelInfo;
  onChange: (emotion: string, filename: string) => void;
}) {
  return (
    <div className="mt-2 rounded-lg border border-line bg-surface-inset p-2">
      <p className="mb-1.5 text-[11px] text-ink-soft">
        为每个情绪指定图片（未指定的情绪用内置占位立绘）
      </p>
      <div className="space-y-1">
        {PORTRAIT_IDS.map((emotion) => (
          <label key={emotion} className="flex items-center gap-2 text-[11px]">
            <span className="w-14 shrink-0 text-ink-muted">{PORTRAIT_LABELS[emotion]}</span>
            <select
              value={model.expressionMap[emotion] ?? ""}
              onChange={(event) => onChange(emotion, event.target.value)}
              className={`min-w-0 flex-1 rounded border border-line bg-surface-panel px-1.5 py-1 text-[11px] text-ink ${FOCUS_RING}`}
            >
              <option value="">（未指定）</option>
              {model.images.map((image) => (
                <option key={image} value={image}>
                  {image}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </div>
  );
}

export function ModelLibraryPanel({ models }: { models: AvatarModelsState }) {
  const {
    models: list,
    loading,
    error,
    selectedId,
    select,
    upload,
    remove,
    updateMapping,
    clearError,
  } = models;

  const [kind, setKind] = useState<AvatarModelKind>("live2d");
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (files.length === 0 || busy) return;
    setBusy(true);
    setNotice(null);
    clearError();
    try {
      const model = await upload({ kind, name, files });
      setNotice(`已上传「${model.name}」`);
      setName("");
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch {
      // 失败原因由 hook 写进 error，这里只管收尾
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(model: AvatarModelInfo) {
    setNotice(null);
    try {
      await remove(model.id);
      setNotice(`已删除「${model.name}」`);
    } catch {
      // 同上
    }
  }

  return (
    <section className="mt-4 border-t border-line pt-3">
      <h3 className="text-xs font-medium text-ink">模型库</h3>
      <p className="mt-0.5 text-[11px] leading-relaxed text-ink-soft">
        上传自己的模型（存后端，换浏览器也在）。不选用则一直用项目内置模型。
      </p>

      {/* ---- 上传 ---- */}
      <div className="mt-2 space-y-2">
        <div className="flex gap-2">
          <select
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as AvatarModelKind);
              setFiles([]);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
            aria-label="模型类型"
            className={`rounded-lg border border-line bg-surface-inset px-2 py-1.5 text-xs text-ink ${FOCUS_RING}`}
          >
            <option value="live2d">Live2D 模型包</option>
            <option value="images">静态立绘</option>
          </select>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="模型名称（可留空）"
            aria-label="模型名称"
            className={`min-w-0 flex-1 rounded-lg border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink placeholder:text-ink-faint ${FOCUS_RING}`}
          />
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={kind === "live2d" ? ".zip,application/zip" : "image/*"}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
          aria-label={kind === "live2d" ? "选择模型包（zip）" : "选择图片"}
          className={`block w-full text-[11px] text-ink-soft file:mr-2 file:rounded-lg file:border-0 file:bg-surface-raised file:px-2.5 file:py-1.5 file:text-[11px] file:text-ink-muted hover:file:bg-surface-hover ${FOCUS_RING}`}
        />
        <p className="text-[11px] text-ink-soft">
          {kind === "live2d"
            ? "选择模型的 zip 包（含 .moc3 与贴图；只取第一个文件）。"
            : "选择若干张表情图片，上传后再逐个指定对应情绪。"}
        </p>

        <button
          type="button"
          onClick={handleUpload}
          disabled={files.length === 0 || busy}
          className={`w-full rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint ${FOCUS_RING}`}
        >
          {busy ? "上传中…" : files.length > 0 ? `上传 ${files.length} 个文件` : "上传"}
        </button>

        {notice && (
          <p role="status" className="text-[11px] text-success-text">
            {notice}
          </p>
        )}
        {error && (
          <p role="alert" className="break-words text-[11px] text-danger-text">
            操作失败：{error}
          </p>
        )}
      </div>

      {/* ---- 清单 ---- */}
      <div className="mt-3">
        <p className="text-[11px] text-ink-soft">
          当前使用：
          <span className="text-ink-muted">
            {list.find((model) => model.id === selectedId)?.name ?? "项目内置模型"}
          </span>
          {loading && "（加载中…）"}
        </p>

        <ul className="mt-2 space-y-1.5">
          {/* 内置模型始终可选，是"取消选择"的出口 */}
          <li
            className={`rounded-lg border px-2.5 py-2 ${
              selectedId ? "border-line" : "border-accent bg-accent-soft"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-[11px] font-medium text-ink">项目内置模型</p>
                <p className="text-[11px] text-ink-soft">Live2D（public/live2d）+ 占位立绘</p>
              </div>
              {!selectedId ? (
                <span className="shrink-0 text-[11px] text-accent-text">使用中</span>
              ) : (
                <button
                  type="button"
                  onClick={() => select("")}
                  className={`shrink-0 rounded-lg border border-line px-2 py-1 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover ${FOCUS_RING}`}
                >
                  使用
                </button>
              )}
            </div>
          </li>

          {list.length === 0 && !loading && (
            <li className="px-1 py-2 text-[11px] text-ink-soft">
              还没有上传过模型。上传后就能在这里选用。
            </li>
          )}

          {list.map((model) => {
            const isSelected = model.id === selectedId;
            return (
              <li
                key={model.id}
                className={`rounded-lg border px-2.5 py-2 ${
                  isSelected ? "border-accent bg-accent-soft" : "border-line"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-[11px] font-medium text-ink">
                      {model.name}
                      <span className="ml-1.5 rounded bg-surface-raised px-1.5 py-0.5 text-[10px] text-ink-muted">
                        {KIND_LABEL[model.kind]}
                      </span>
                    </p>
                    <p className="mt-0.5 text-[11px] text-ink-soft">{describeModel(model)}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {isSelected ? (
                      <span className="text-[11px] text-accent-text">使用中</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => select(model.id)}
                        className={`rounded-lg border border-line px-2 py-1 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover ${FOCUS_RING}`}
                      >
                        使用
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleRemove(model)}
                      className={`rounded-lg border border-line px-2 py-1 text-[11px] text-danger-text transition-colors hover:bg-danger-soft ${FOCUS_RING}`}
                    >
                      删除
                    </button>
                  </div>
                </div>

                {/* 静态立绘：选中后展开逐个指定情绪 */}
                {isSelected && model.kind === "images" && model.images.length > 0 && (
                  <EmotionMapping
                    model={model}
                    onChange={(emotion, filename) => {
                      const next = { ...model.expressionMap };
                      if (filename) next[emotion] = filename;
                      else delete next[emotion];
                      void updateMapping(model.id, next);
                    }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
