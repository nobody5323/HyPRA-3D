"use client";

/**
 * 个人记忆（知识库）面板：用户上传自己的语料，让「苏澄」在后续对话中引用。
 *
 * 对应后端三个接口（backend/app/api/knowledge.py）：
 *   POST   /knowledge/upload       解析 → 清洗 → 判重 → 分块 → 入库
 *   GET    /knowledge/list         列出该陪伴对象的全部文档
 *   DELETE /knowledge/{doc_id}     删除整篇文档及其全部分块
 *
 * 关键交互设计：
 * - **判重不自动覆盖**：后端命中判重返回 409，这里弹出「覆盖 / 取消」二选一；
 *   覆盖走 `force=true` 重提，后端会复用原 doc_id（「覆盖」不会是「并存两份」）。
 * - **记忆隔离**：`companionId` 即对话的 persona_id；切换陪伴对象时列表整体重载，
 *   避免把 A 角色的私人资料显示在 B 角色下。
 * - **删除二次确认**：不用 window.confirm（阻塞式原生弹窗），改为内联两态按钮。
 *
 * 无障碍要点（延续 summaries/audit-frontend 的约定）：
 * - 折叠触发器暴露 `aria-expanded` / `aria-controls`；
 * - 结果提示 `role="status"`、失败提示 `role="alert"`（读屏可听）；
 * - 所有输入都有可见或 sr-only 的关联标签；忙碌态用 `aria-busy` 表达。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, deleteKnowledge, listKnowledge, uploadKnowledge } from "@/lib/api";
import type { KnowledgeDoc, KnowledgeDuplicateDetail } from "@/lib/types";

/** 后端支持的扩展名（app/memory/knowledge/parser.py 的 SUPPORTED_SUFFIXES） */
const ACCEPT = ".txt,.text,.log,.csv,.tsv,.md,.markdown,.pdf,.docx";

/** 来源类型 → 展示标签（后端 parser 给出的 source_type） */
const SOURCE_LABEL: Record<string, string> = {
  pdf: "PDF",
  docx: "Word",
  md: "Markdown",
  text: "纯文本",
};

/** 判重级别 → 人话（后端 knowledge.py 的 kind 取值：file / content / similar） */
const DUPLICATE_REASON_LABEL: Record<string, string> = {
  file: "文件内容完全相同",
  content: "清洗后的正文完全相同",
  similar: "正文高度相似",
};

/**
 * 409 detail 的形状校验。
 *
 * 后端 `detail` 在 FastAPI 里是 unknown（字符串或结构体），直接断言成
 * `KnowledgeDuplicateDetail` 会在后端换错误体时静默渲染出 undefined。
 */
function asDuplicateDetail(detail: unknown): KnowledgeDuplicateDetail | null {
  if (!detail || typeof detail !== "object") return null;
  const candidate = detail as Partial<KnowledgeDuplicateDetail>;
  const existing = candidate.existing as Partial<KnowledgeDuplicateDetail["existing"]> | undefined;
  if (typeof candidate.reason !== "string") return null;
  if (!existing || typeof existing.title !== "string") return null;
  return {
    reason: candidate.reason,
    similarity: typeof candidate.similarity === "number" ? candidate.similarity : 0,
    existing: {
      doc_id: existing.doc_id ?? "",
      title: existing.title,
      chunk_count: existing.chunk_count ?? 0,
    },
    hint: typeof candidate.hint === "string" ? candidate.hint : "",
  };
}

/** ISO 时间串 → 本地「年-月-日 时:分」 */
function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function KnowledgePanel({
  companionId,
  disabled = false,
  onUploaded,
}: {
  /** 陪伴对象 id（= 对话的 persona_id），决定记忆命名空间 */
  companionId: string;
  /** 对话进行中时禁止改动（避免与对话争用向量库/embedding） */
  disabled?: boolean;
  /** 上传成功回调（页面据此提示「下一轮对话即可引用」） */
  onUploaded?: (doc: KnowledgeDoc) => void;
}) {
  const [open, setOpen] = useState(false);
  /** null = 尚未加载；[] = 已加载但为空 */
  const [docs, setDocs] = useState<KnowledgeDoc[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  /** 判重命中待确认（非 null 时展示「覆盖 / 取消」） */
  const [duplicate, setDuplicate] = useState<KnowledgeDuplicateDetail | null>(null);
  /** 正在二次确认删除的文档 id */
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  /**
   * 请求序号：只接受最新一次列表请求的结果。
   *
   * 切换陪伴对象时 `companionId` 变化会产生新请求；若旧请求后返回，
   * 会把上一个角色的资料写进列表（记忆隔离在界面上被破坏）。
   */
  const requestSeqRef = useRef(0);
  const panelId = "knowledge-panel";
  const titleId = "knowledge-title";
  const busyNow = busy || loading;

  const reload = useCallback(async () => {
    const seq = ++requestSeqRef.current;
    setLoading(true);
    try {
      const items = await listKnowledge(companionId);
      if (seq !== requestSeqRef.current) return; // 已有更新的请求 → 丢弃本次结果
      setDocs(items);
      setError(null);
    } catch (err) {
      if (seq !== requestSeqRef.current) return;
      setDocs([]);
      setError(err instanceof Error ? err.message : "读取个人记忆列表失败");
    } finally {
      if (seq === requestSeqRef.current) setLoading(false);
    }
  }, [companionId]);

  // 切换陪伴对象：清掉上一个角色的资料、提示与待上传内容。
  //
  // 必须一并作废在途请求（requestSeqRef 自增）——否则旧响应会把上一个角色的
  // 资料写进列表，折叠标题的「N 篇已入库」就会张冠李戴。
  useEffect(() => {
    requestSeqRef.current += 1;
    setDocs(null);
    setStatus(null);
    setError(null);
    setDuplicate(null);
    setPendingDelete(null);
    // 已选文件/粘贴内容属于上一个角色：留在界面上会被静默写入新角色的命名空间
    setFile(null);
    setText("");
    setTitle("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [companionId]);

  // 展开时才拉列表（折叠状态不产生请求）
  useEffect(() => {
    if (!open) return;
    void reload();
  }, [open, reload]);

  /** 上传（force=true 表示用户已确认覆盖） */
  async function submit(force: boolean) {
    const trimmedText = text.trim();
    if (!file && !trimmedText) {
      setError("请选择文件，或粘贴一段文本");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await uploadKnowledge({
        companionId,
        file: file ?? undefined,
        // 后端优先使用 file；仅在无文件时才送粘贴文本
        text: file ? undefined : trimmedText,
        title: title.trim() || undefined,
        force,
      });
      setDuplicate(null);
      setFile(null);
      setText("");
      setTitle("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      setStatus(
        result.replaced
          ? `已覆盖《${result.doc.title}》并重新入库（${result.doc.chunk_count} 个片段）`
          : `已入库《${result.doc.title}》：${result.doc.chunk_count} 个片段，` +
              `清洗移除 ${Math.round(result.cleaned_ratio * 100)}%`,
      );
      await reload();
      onUploaded?.(result.doc);
    } catch (err) {
      const dup =
        err instanceof ApiError && err.status === 409 ? asDuplicateDetail(err.detail) : null;
      if (dup) {
        setDuplicate(dup); // 保留已选文件/文本，用户点「覆盖」时原样重提
      } else {
        setError(err instanceof Error ? err.message : "上传失败");
      }
    } finally {
      setBusy(false);
    }
  }

  /** 删除：仅在二次确认后调用 */
  async function confirmDelete(doc: KnowledgeDoc) {
    setPendingDelete(null);
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await deleteKnowledge(doc.doc_id, companionId);
      setStatus(`已删除《${doc.title}》（移除 ${result.removed_chunks} 个片段）`);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby={titleId}
      className="rounded-xl border border-line bg-surface-panel"
    >
      <h2 id={titleId} className="sr-only">
        个人记忆
      </h2>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((prev) => !prev)}
        className="focus-ring flex w-full items-center justify-between rounded-xl px-4 py-3 text-left transition-colors hover:bg-surface-hover"
      >
        <span className="flex items-baseline gap-2">
          <span className="text-xs text-ink-muted">个人记忆</span>
          <span className="text-xs text-ink-faint">
            {docs === null ? "上传你的资料，对话时自动引用" : `${docs.length} 篇已入库`}
          </span>
        </span>
        <span aria-hidden="true" className="text-xs text-ink-soft">
          {open ? "收起" : "展开"}
        </span>
      </button>

      {open && (
        <div
          id={panelId}
          aria-busy={busyNow}
          className="max-h-[40vh] space-y-3 overflow-y-auto border-t border-line px-4 py-3"
        >
          {/* ---- 上传区 ---- */}
          <div className="space-y-2">
            <label className="block">
              <span className="text-xs text-ink-muted">选择文件</span>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT}
                disabled={disabled || busy}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setDuplicate(null);
                  setError(null);
                }}
                className="focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-2 py-1.5 text-xs text-ink-muted file:mr-2 file:rounded-md file:border-0 file:bg-surface-hover file:px-2.5 file:py-1 file:text-xs file:text-ink-muted disabled:cursor-not-allowed"
              />
            </label>

            <label className="block">
              <span className="text-xs text-ink-muted">
                或粘贴文本{file ? "（已选文件，此项将被忽略）" : ""}
              </span>
              <textarea
                value={text}
                onChange={(event) => {
                  setText(event.target.value);
                  setDuplicate(null);
                  setError(null);
                }}
                rows={3}
                disabled={disabled || busy || Boolean(file)}
                placeholder="例如：我的工作节奏、在意的人和事、希望被怎么称呼…"
                className="focus-ring mt-1 w-full resize-y rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent disabled:cursor-not-allowed disabled:text-ink-faint"
              />
            </label>

            <label className="block">
              <span className="text-xs text-ink-muted">标题（可留空，默认取文件名）</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                disabled={disabled || busy}
                className="focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent disabled:cursor-not-allowed"
              />
            </label>

            <button
              type="button"
              onClick={() => void submit(false)}
              disabled={disabled || busy || (!file && !text.trim())}
              className="focus-ring w-full rounded-lg bg-accent px-3 py-2 text-sm font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
            >
              {busy ? "处理中…" : "加入个人记忆"}
            </button>

            <p className="text-xs leading-relaxed text-ink-faint">
              支持 .txt / .md / .pdf / .docx（.doc 请先另存为 .docx）。入库后会参与
              后续对话的检索，并在「本轮记忆」里显示召回条数。
            </p>
          </div>

          {/* ---- 判重确认（409）---- */}
          {duplicate && (
            <div
              role="alert"
              className="rounded-lg border border-warning/40 bg-warning-soft px-3 py-2 text-xs text-warning-text"
            >
              <p className="font-medium">
                发现相似文档：《{duplicate.existing.title}》
              </p>
              <p className="mt-1 leading-relaxed">
                判重级别：{DUPLICATE_REASON_LABEL[duplicate.reason] ?? duplicate.reason}
                {duplicate.similarity > 0 && `（相似度 ${duplicate.similarity.toFixed(3)}）`}
                ，该文档现有 {duplicate.existing.chunk_count} 个片段。
                {duplicate.hint}
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => void submit(true)}
                  disabled={disabled || busy}
                  className="focus-ring rounded-md bg-warning px-3 py-1 font-medium text-ink-on transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  覆盖原文档
                </button>
                <button
                  type="button"
                  onClick={() => setDuplicate(null)}
                  disabled={busy}
                  className="focus-ring rounded-md border border-line px-3 py-1 text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed"
                >
                  取消
                </button>
              </div>
            </div>
          )}

          {error && (
            <p role="alert" className="break-words text-xs leading-relaxed text-danger-text">
              {error}
            </p>
          )}
          {status && (
            <p role="status" className="break-words text-xs leading-relaxed text-success-text">
              {status}
            </p>
          )}

          {/* ---- 已入库列表 ---- */}
          <div className="border-t border-line pt-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs text-ink-soft">已入库资料</h3>
              <button
                type="button"
                onClick={() => void reload()}
                disabled={disabled || loading || busy}
                className="focus-ring rounded-md px-2 py-0.5 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:text-ink-faint"
              >
                {loading ? "刷新中…" : "刷新"}
              </button>
            </div>

            {docs === null && <p className="mt-2 text-xs text-ink-faint">加载中…</p>}

            {docs !== null && docs.length === 0 && !loading && (
              <p className="mt-2 text-xs leading-relaxed text-ink-faint">
                还没有资料。上传后，苏澄会在相关话题的对话中引用它们。
              </p>
            )}

            {docs !== null && docs.length > 0 && (
              <ul className="mt-2 space-y-2">
                {docs.map((doc) => (
                  <li
                    key={doc.doc_id}
                    className="rounded-lg border border-line bg-surface-inset px-3 py-2"
                  >
                    <p className="break-words text-sm text-ink">{doc.title}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-soft">
                      <span>{SOURCE_LABEL[doc.source_type] ?? doc.source_type}</span>
                      <span className="tabular-nums">{doc.chunk_count} 个片段</span>
                      <span className="tabular-nums">{formatDate(doc.created_at)}</span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      {pendingDelete === doc.doc_id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => void confirmDelete(doc)}
                            disabled={disabled || busy}
                            className="focus-ring rounded-md bg-danger px-2.5 py-1 text-xs font-medium text-ink-on transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            确认删除
                          </button>
                          <button
                            type="button"
                            onClick={() => setPendingDelete(null)}
                            className="focus-ring rounded-md border border-line px-2.5 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover"
                          >
                            取消
                          </button>
                          <span className="text-xs text-ink-faint">删除后无法恢复</span>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setPendingDelete(doc.doc_id)}
                          disabled={disabled || busy}
                          className="focus-ring rounded-md border border-line px-2.5 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed"
                        >
                          删除
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
