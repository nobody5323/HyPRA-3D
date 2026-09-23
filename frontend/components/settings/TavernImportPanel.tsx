"use client";

/**
 * 酒馆会话 → 长期记忆的导入面板（`AGENTS.md` P4 的界面入口）。
 *
 * 为什么独立成一块，而不是塞进 tavern-bridge 插件的配置表单里：
 * 「导入到哪个陪伴对象」是**会话属性**（当前 persona），不是插件配置——
 * 同一份插件配置下，不同陪伴对象的记忆是**隔离**的（§8.2 的 `companion:{id}`）。
 * 塞进 settings 会误导用户以为「填一次就通用」。
 *
 * 未就绪（409 / 404）按**预期状态**渲染指路文案，不弹错误框：
 * 用户的第一步本来就该是「先启用插件、填目录」，这不是失败。
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, getTavernBridgeStatus, importTavernMemory } from "@/lib/api/client";
import type { TavernBridgeStatus, TavernImportResult } from "@/lib/api/types";

const COUNT_LABEL: Record<string, string> = {
  entries: "世界书条目",
  characters: "角色卡",
  sessions: "会话",
};

const HINT_CLASS = "mt-1 text-[11px] leading-relaxed text-ink-faint";

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

export function TavernImportPanel({ companionId }: { companionId: string }) {
  const [status, setStatus] = useState<TavernBridgeStatus | null>(null);
  /** 未就绪的指路文案（插件未启用 / 未安装）；与 `error` 区分：那是真故障 */
  const [blocked, setBlocked] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TavernImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 强制重导的二次确认：重新导入会重复写入记忆，不能一键直达 */
  const [forceArmed, setForceArmed] = useState(false);

  const load = useCallback(async () => {
    if (!companionId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setStatus(await getTavernBridgeStatus(companionId));
      setBlocked(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setStatus(null);
        setBlocked("尚未生效：请先启用「酒馆数据接入」并填写酒馆数据目录。");
      } else if (err instanceof ApiError && err.status === 404) {
        setStatus(null);
        setBlocked("酒馆接入插件未安装。");
      } else {
        setStatus(null);
        setError(errorText(err, "读取酒馆接入状态失败"));
      }
    } finally {
      setLoading(false);
    }
  }, [companionId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleImport(force: boolean) {
    setBusy(true);
    setError(null);
    setForceArmed(false);
    try {
      const next = await importTavernMemory(companionId, force);
      setResult(next);
      await load(); // 刷新「已导入」计数，否则进度条不动会让人以为没生效
    } catch (err) {
      setError(errorText(err, "导入失败"));
    } finally {
      setBusy(false);
    }
  }

  if (!companionId) {
    return (
      <section className="rounded-lg border border-line bg-surface-panel p-3">
        <h3 className="text-xs font-medium text-ink-muted">酒馆记忆导入</h3>
        <p className={HINT_CLASS}>请先选择一个陪伴对象：导入的记忆归属于它。</p>
      </section>
    );
  }

  const importedCount = status?.imported_sessions.length ?? 0;
  const available = status?.available ?? {};

  return (
    <section
      aria-labelledby="tavern-import-title"
      className="rounded-lg border border-line bg-surface-panel p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="tavern-import-title" className="text-xs font-medium text-ink-muted">
          酒馆记忆导入
        </h3>
        <button
          type="button"
          className="focus-ring rounded px-1.5 py-0.5 text-[11px] text-ink-faint hover:text-ink-muted"
          disabled={loading || busy}
          onClick={() => void load()}
        >
          刷新
        </button>
      </div>

      {loading && <p className={HINT_CLASS}>读取中…</p>}

      {!loading && blocked && <p className={HINT_CLASS}>{blocked}</p>}

      {!loading && status && (
        <>
          <p className={HINT_CLASS}>
            数据目录：<span className="break-all font-mono">{status.root || "（未解析）"}</span>
          </p>

          <p className="mt-1 text-[11px] text-ink-muted">
            {Object.entries(available).length === 0
              ? "未读到内容"
              : Object.entries(available)
                  .map(([key, count]) => `${COUNT_LABEL[key] ?? key} ${count}`)
                  .join(" · ")}
          </p>

          <p className="mt-1 text-[11px] text-ink-muted">
            已导入到当前陪伴对象：<span className="tabular-nums">{importedCount}</span> 个会话
          </p>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="focus-ring rounded-lg border border-line bg-surface-raised px-2.5 py-1 text-[11px] text-ink hover:border-accent disabled:opacity-50"
              disabled={busy}
              onClick={() => void handleImport(false)}
            >
              {busy ? "导入中…" : "导入记忆"}
            </button>

            {importedCount > 0 &&
              (forceArmed ? (
                <button
                  type="button"
                  className="focus-ring rounded-lg border border-danger-text/40 px-2.5 py-1 text-[11px] text-danger-text disabled:opacity-50"
                  disabled={busy}
                  onClick={() => void handleImport(true)}
                >
                  确认重新导入 {importedCount} 个会话（会重复写入记忆）
                </button>
              ) : (
                <button
                  type="button"
                  className="focus-ring rounded-lg px-2.5 py-1 text-[11px] text-ink-faint hover:text-ink-muted disabled:opacity-50"
                  disabled={busy}
                  onClick={() => setForceArmed(true)}
                >
                  重新导入…
                </button>
              ))}
          </div>

          <p className={HINT_CLASS}>
            已导入的会话会被跳过，因此重复点「导入记忆」是安全的。
          </p>

          {status.warnings.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-[11px] text-warning-text">
                读取告警 {status.warnings.length} 条
              </summary>
              <ul className="mt-1 flex flex-col gap-0.5">
                {status.warnings.slice(0, 10).map((warning) => (
                  <li key={warning} className="text-[11px] text-ink-faint">
                    {warning}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {result && (
        <p role="status" className="mt-2 text-[11px] leading-relaxed text-success-text">
          已导入 {result.sessions_imported} 个会话（跳过 {result.sessions_skipped}）、
          {result.turns} 轮对话、{result.facts} 条事实、{result.memories} 条情景记忆。
        </p>
      )}

      {result && result.warnings.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] text-warning-text">
            导入告警 {result.warnings.length} 条
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {result.warnings.slice(0, 10).map((warning) => (
              <li key={warning} className="text-[11px] text-ink-faint">
                {warning}
              </li>
            ))}
          </ul>
        </details>
      )}

      {error && (
        <p role="alert" className="mt-2 text-[11px] text-danger-text">
          {error}
        </p>
      )}
    </section>
  );
}
