"use client";

/**
 * 酒馆（SillyTavern）预设面板：导入 → 选择 → 编辑 → 导出。
 *
 * 对应后端 `/chat/st-presets*` 一组接口（backend/app/api/chat.py）：
 *   GET    /chat/st-presets              清单（不含正文）
 *   POST   /chat/st-presets/import       导入（解析失败不落盘）
 *   GET    /chat/st-presets/{id}         详情
 *   PATCH  /chat/st-presets/{id}         写覆盖层（编辑器负责）
 *   POST   /chat/st-presets/{id}/reset   回到导入时
 *   GET    /chat/st-presets/{id}/export  导出为酒馆兼容 JSON
 *   DELETE /chat/st-presets/{id}         删除
 *
 * 关键交互设计：
 * - **原始文件只读**：界面上的编辑写覆盖层，「重置」永远可用（不会把用户的预设改坏）；
 * - **删除二次确认**：内联两态按钮，不用阻塞式 window.confirm；
 * - **选中即生效**：选定后 `POST /chat` 带上 `st_preset_id`，下一轮按该预设组装；
 *   选「不使用」则回到内置分层路径。
 *
 * 合规（AGENTS.md §6）：面板里出现的提示词正文全部来自**用户自己导入的文件**，
 * 存在后端本地目录（已 gitignore），不随仓库或镜像分发。本项目不内置任何
 * SillyTavern 或社区预设的提示词原文。
 *
 * 无障碍：折叠触发器暴露 aria-expanded/aria-controls；导入区支持键盘选择文件
 * 与拖放两条路径；结果提示 role="status"、失败 role="alert"。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { StPresetAdaptPanel } from "@/components/StPresetAdaptPanel";
import { StPresetEditor } from "@/components/StPresetEditor";
import {
  ApiError,
  deleteStPreset,
  exportStPreset,
  getStPreset,
  importStPreset,
  listStPresets,
  resetStPreset,
} from "@/lib/api";
import { formatImportedAt } from "@/lib/st-preset";
import type { StPresetCatalog, StPresetDetail, StPresetSummary } from "@/lib/types";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

const PANEL_ID = "st-preset-panel";

/** 触发浏览器下载一份 JSON（导出预设用）。 */
function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

export function StPresetPanel({
  value,
  onChange,
  disabled = false,
  lastRun,
}: {
  /** 当前选用的预设 id；"" = 不使用（走内置分层路径） */
  value: string;
  onChange: (id: string) => void;
  /** 对话进行中时禁止改动预设（改到一半发消息会得到不一致的组装结果） */
  disabled?: boolean;
  /** 上一轮 ST 组装的元信息（来自 POST /chat 的 st_preset 字段） */
  lastRun?: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  /** null = 尚未加载（区别于「已加载但为空」） */
  const [catalog, setCatalog] = useState<StPresetCatalog | null>(null);
  const [detail, setDetail] = useState<StPresetDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [dropActive, setDropActive] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  /** 详情请求序号：快速切换预设时丢弃过期响应 */
  const detailSeqRef = useRef(0);

  const reload = useCallback(async () => {
    const next = await listStPresets();
    setCatalog(next);
    return next;
  }, []);

  // 首次展开时拉清单（面板收起时不打扰后端）
  useEffect(() => {
    if (!open) return;
    void reload();
  }, [open, reload]);

  // 选中项变化 → 拉详情（编辑器用 key 重挂载，内部状态不会串）
  useEffect(() => {
    if (!open || !value) {
      setDetail(null);
      return;
    }
    const seq = ++detailSeqRef.current;
    setDetailLoading(true);
    getStPreset(value)
      .then((res) => {
        if (seq !== detailSeqRef.current) return;
        setDetail(res.detail);
        setError(null);
      })
      .catch((err: unknown) => {
        if (seq !== detailSeqRef.current) return;
        setDetail(null);
        setError(errorText(err, "读取预设详情失败"));
      })
      .finally(() => {
        if (seq === detailSeqRef.current) setDetailLoading(false);
      });
  }, [open, value]);

  const handleImport = useCallback(
    async (file: File) => {
      setImporting(true);
      setError(null);
      setStatus(null);
      try {
        const text = await file.text();
        const res = await importStPreset({ content: text, sourceFile: file.name });
        const name = "name" in res.preset ? res.preset.name : res.detail.id;
        await reload();
        onChange(res.detail.id);
        setStatus(
          `已导入「${name}」（${res.detail.prompts.length} 条）${
            res.detail.unsupported.length > 0
              ? `，其中 ${res.detail.unsupported.length} 项在本项目不生效`
              : ""
          }`,
        );
      } catch (err: unknown) {
        setError(errorText(err, "导入失败"));
      } finally {
        setImporting(false);
      }
    },
    [onChange, reload],
  );

  const handleDelete = useCallback(
    async (presetId: string) => {
      setError(null);
      setStatus(null);
      try {
        await deleteStPreset(presetId);
        if (value === presetId) onChange("");
        await reload();
        setPendingDelete(null);
        setStatus(`已删除预设「${presetId}」`);
      } catch (err: unknown) {
        setError(errorText(err, "删除失败"));
      }
    },
    [onChange, reload, value],
  );

  const handleReset = useCallback(
    async (presetId: string) => {
      setError(null);
      setStatus(null);
      try {
        const updated = await resetStPreset(presetId);
        if (presetId === value) setDetail(updated);
        await reload();
        setStatus(`已把「${presetId}」恢复到导入时的状态`);
      } catch (err: unknown) {
        setError(errorText(err, "重置失败"));
      }
    },
    [reload, value],
  );

  const handleExport = useCallback(async (presetId: string) => {
    setError(null);
    setStatus(null);
    try {
      const data = await exportStPreset(presetId);
      downloadJson(`${presetId}.json`, data);
      setStatus(`已导出 ${presetId}.json（可直接在酒馆里导入）`);
    } catch (err: unknown) {
      setError(errorText(err, "导出失败"));
    }
  }, []);

  const onDetailSaved = useCallback(
    (updated: StPresetDetail) => {
      setDetail(updated);
      // 清单里的「已编辑」标记要跟着变，否则用户看不出自己改过
      void reload();
    },
    [reload],
  );

  const presets: StPresetSummary[] = catalog?.presets ?? [];
  const current = presets.find((preset) => preset.id === value) ?? null;
  const summaryLabel = current ? current.name : value ? value : "未使用";

  /** 上一轮的实际生效情况（让用户看得到“这个预设在我这里真的起作用了吗”） */
  const runNotes: string[] = [];
  if (value && lastRun && Object.keys(lastRun).length > 0) {
    const used = Array.isArray(lastRun.used_markers) ? lastRun.used_markers.length : 0;
    const empty = Array.isArray(lastRun.empty_markers) ? lastRun.empty_markers.length : 0;
    const inChat = typeof lastRun.in_chat_count === "number" ? lastRun.in_chat_count : 0;
    const macros = Array.isArray(lastRun.unresolved_macros) ? lastRun.unresolved_macros.length : 0;
    runNotes.push(`命中槽位 ${used} 个`);
    if (empty > 0) runNotes.push(`${empty} 个槽位本轮为空`);
    if (inChat > 0) runNotes.push(`深层注入 ${inChat} 条`);
    if (macros > 0) runNotes.push(`未识别宏 ${macros} 个`);
  }

  return (
    <section className="rounded-2xl border border-line bg-surface-panel">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={PANEL_ID}
        onClick={() => setOpen((prev) => !prev)}
        className={`flex w-full items-center justify-between gap-2 rounded-2xl px-4 py-2.5 text-left text-sm text-ink transition-colors hover:bg-surface-hover ${FOCUS_RING}`}
      >
        <span className="min-w-0">
          <span className="text-xs text-ink-soft">酒馆预设 · </span>
          <span className="truncate">{summaryLabel}</span>
        </span>
        <span className="shrink-0 text-xs text-ink-soft" aria-hidden="true">
          {open ? "收起" : "展开"}
        </span>
      </button>

      {open && (
        <div id={PANEL_ID} className="flex flex-col gap-3 border-t border-line px-4 py-3">
          <p className="text-xs leading-relaxed text-ink-faint">
            导入你在酒馆里用的预设 JSON，本轮对话就按它的条目顺序、启用状态与注入深度组装提示词。
            预设正文只保存在本机后端目录，不会随项目分发。
          </p>

          {runNotes.length > 0 && (
            <p className="rounded-xl border border-line bg-surface-inset px-3 py-2 text-xs text-ink-soft">
              上一轮按「{summaryLabel}」组装：{runNotes.join(" · ")}
            </p>
          )}

          {/* ---- 导入 ---- */}
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDropActive(true);
            }}
            onDragLeave={() => setDropActive(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDropActive(false);
              const file = event.dataTransfer.files?.[0];
              if (file) void handleImport(file);
            }}
            className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border border-dashed px-3 py-2 transition-colors ${
              dropActive ? "border-accent bg-accent-soft/50" : "border-line bg-surface-inset"
            }`}
          >
            <span className="text-xs text-ink-soft">把预设 JSON 拖到这里，或</span>
            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,application/json"
                aria-label="选择预设文件"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  // 清空 value：否则选择同一个文件第二次不触发 change
                  event.target.value = "";
                  if (file) void handleImport(file);
                }}
              />
              <button
                type="button"
                disabled={importing}
                onClick={() => fileInputRef.current?.click()}
                className={`rounded-full bg-accent px-3 py-1 text-xs text-ink-on transition-colors hover:bg-accent-hover disabled:opacity-50 ${FOCUS_RING}`}
              >
                {importing ? "导入中…" : "选择预设文件"}
              </button>
            </div>
          </div>

          {/* ---- 结果提示 ---- */}
          {status && (
            <p role="status" className="text-xs text-success-text">
              {status}
            </p>
          )}
          {error && (
            <p role="alert" className="text-xs text-danger-text">
              {error}
            </p>
          )}

          {/* ---- 预设清单 ---- */}
          {catalog === null ? (
            <p className="text-xs text-ink-soft">后端未连接，暂时读不到已导入的预设。</p>
          ) : presets.length === 0 ? (
            <p className="text-xs text-ink-soft">还没有导入任何预设。</p>
          ) : (
            <ul className="flex flex-col gap-2">
              <li className="flex items-center gap-2 rounded-xl border border-line bg-surface-inset px-3 py-2">
                <input
                  type="radio"
                  id="st-preset-none"
                  name="st-preset-choice"
                  checked={value === ""}
                  onChange={() => onChange("")}
                  className={`accent-accent ${FOCUS_RING}`}
                />
                <label htmlFor="st-preset-none" className="flex-1 text-xs text-ink-muted">
                  不使用酒馆预设（走内置分层组装）
                </label>
              </li>

              {presets.map((preset) => {
                const selected = value === preset.id;
                return (
                  <li
                    key={preset.id}
                    className={`flex flex-col gap-1.5 rounded-xl border px-3 py-2 ${
                      selected ? "border-accent bg-accent-soft/40" : "border-line bg-surface-inset"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <input
                        type="radio"
                        id={`st-preset-${preset.id}`}
                        name="st-preset-choice"
                        checked={selected}
                        onChange={() => onChange(preset.id)}
                        className={`accent-accent ${FOCUS_RING}`}
                      />
                      <label
                        htmlFor={`st-preset-${preset.id}`}
                        className="min-w-0 flex-1 text-xs text-ink"
                      >
                        <span className="truncate font-medium">{preset.name}</span>
                        <span className="ml-2 text-ink-soft">
                          {preset.prompt_count} 条（启用 {preset.enabled_count}）
                        </span>
                        {preset.imported_at && (
                          <span className="ml-2 text-ink-faint">
                            {formatImportedAt(preset.imported_at)}
                          </span>
                        )}
                      </label>
                      {preset.has_override && (
                        <span className="shrink-0 rounded-full bg-surface-raised px-2 py-0.5 text-xs text-ink-soft ring-1 ring-line">
                          已编辑
                        </span>
                      )}
                      {preset.unsupported.length > 0 && (
                        <span className="shrink-0 rounded-full bg-warning-soft px-2 py-0.5 text-xs text-warning-text ring-1 ring-warning/30">
                          {preset.unsupported.length} 项不生效
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void handleExport(preset.id)}
                        className={`rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-ink-muted ring-1 ring-line transition-colors hover:bg-surface-hover ${FOCUS_RING}`}
                      >
                        导出
                      </button>
                      <button
                        type="button"
                        disabled={!preset.has_override || disabled}
                        onClick={() => void handleReset(preset.id)}
                        className={`rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-ink-muted ring-1 ring-line transition-colors hover:bg-surface-hover disabled:opacity-40 ${FOCUS_RING}`}
                      >
                        恢复导入时
                      </button>
                      {pendingDelete === preset.id ? (
                        <>
                          <span className="text-xs text-danger-text">确认删除？</span>
                          <button
                            type="button"
                            disabled={disabled}
                            onClick={() => void handleDelete(preset.id)}
                            className={`rounded-full bg-danger px-2.5 py-0.5 text-xs text-ink-on transition-colors hover:bg-danger-text disabled:opacity-50 ${FOCUS_RING}`}
                          >
                            删除
                          </button>
                          <button
                            type="button"
                            onClick={() => setPendingDelete(null)}
                            className={`rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-ink-muted ring-1 ring-line transition-colors hover:bg-surface-hover ${FOCUS_RING}`}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          disabled={disabled}
                          onClick={() => setPendingDelete(preset.id)}
                          className={`rounded-full px-2.5 py-0.5 text-xs text-danger-text transition-colors hover:bg-danger-soft disabled:opacity-40 ${FOCUS_RING}`}
                        >
                          删除
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {/* ---- AI 适配（先试运行，用户确认后才落盘） ---- */}
          {detail && (
            <StPresetAdaptPanel
              // key 强制重挂载：切换预设时清掉上一个预设的建议（否则会把 A 的
              // 对照当成 B 的结果，又把改动写到 B 上）。与下方编辑器同一口径。
              key={detail.id}
              presetId={detail.id}
              onApplied={onDetailSaved}
              disabled={disabled}
            />
          )}

          {/* ---- 编辑器 ---- */}
          {detailLoading && <p className="text-xs text-ink-soft">正在读取预设详情…</p>}
          {detail && (
            <div className="rounded-xl border border-line bg-surface-inset px-3 py-3">
              <StPresetEditor
                key={detail.id}
                presetId={detail.id}
                detail={detail}
                onSaved={onDetailSaved}
                disabled={disabled}
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
