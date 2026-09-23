"use client";

/**
 * 创作工坊：用户自建「角色卡 + 背景故事 + 世界书设定」的编辑面板。
 *
 * 对应后端 `backend/app/api/studio.py`（`/chat/studio/*`）。三块数据由
 * `GET /chat/studio/catalog` 一次取回（角色清单 + 世界书条目 + 写作提示），
 * 每个写操作也会把**刷新后的 catalog** 带回来，因此界面不需要二次拉取。
 *
 * 设计要点：
 * - **内置只读**：内置角色 / 条目在列表里带「内置」徽标，表单置灰；要改就先
 *   「复制为我的角色」。内置**条目**的启用开关是例外——后端把它写成用户侧的
 *   停用偏好，内置 YAML 一字未改。
 * - **归属隔离**：世界书条目可归属到某个角色（`scope`）。给专属角色写设定时
 *   选它，就不会串味到别的角色；新建条目的默认归属是**当前对话角色**。
 * - **写完即生效**：后端在每个写操作后失效并重建对话图，所以新角色下一轮对话
 *   就能用，不需要重启服务。
 *
 * 无障碍：模态对话框（`role="dialog"` + `aria-modal`），Esc / 「关闭」都会归还
 * 焦点；分区用 tablist / tab / tabpanel 语义；结果 `role="status"`、错误 `role="alert"`。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getStudioCatalog } from "@/lib/api";
import type { StudioCatalog } from "@/lib/types";

import { PersonaStudio } from "./PersonaStudio";
import { WorldBookStudio } from "./WorldBookStudio";

type StudioTab = "persona" | "worldbook";

const TABS: Array<{ id: StudioTab; label: string; hint: string }> = [
  { id: "persona", label: "我的角色", hint: "人设 · 背景故事" },
  { id: "worldbook", label: "世界书", hint: "按条件触发的设定" },
];

export function StudioPanel({
  open,
  onClose,
  disabled = false,
  defaultScope,
  panelId = "studio-panel",
  onCatalogChange,
}: {
  open: boolean;
  onClose: () => void;
  /** 对话进行中时禁止改动（与个人记忆面板一致） */
  disabled?: boolean;
  /** 新建世界书条目的默认归属（通常是当前对话角色） */
  defaultScope?: string;
  panelId?: string;
  /** catalog 更新回调（页面据此刷新「陪伴对象」选择器） */
  onCatalogChange?: (catalog: StudioCatalog) => void;
}) {
  const [catalog, setCatalog] = useState<StudioCatalog | null>(null);
  const [tab, setTab] = useState<StudioTab>("persona");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  /** 打开面板前持有焦点的元素：关闭时把焦点还回去 */
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = `${panelId}-title`;
  const tabPanelId = (id: StudioTab) => `${panelId}-tabpanel-${id}`;

  const applyCatalog = useCallback(
    (next: StudioCatalog) => {
      setCatalog(next);
      onCatalogChange?.(next);
    },
    [onCatalogChange],
  );

  /** 拉取工坊数据（打开时、以及加载失败后重试时调用）。 */
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      applyCatalog(await getStudioCatalog());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "读取创作工坊失败");
    } finally {
      setLoading(false);
    }
  }, [applyCatalog]);

  const close = useCallback(() => {
    onClose();
    const previous = restoreFocusRef.current;
    restoreFocusRef.current = null;
    if (previous && previous.isConnected) previous.focus();
  }, [onClose]);

  // 打开：重新拉一次（期间用户可能手改了 backend/data/studio 下的 YAML）+ 聚焦
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    void load();
    closeRef.current?.focus();
  }, [open, load]);

  // Esc 关闭并归还焦点
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, close]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-ink/30 p-3 sm:p-6"
      onClick={(event) => {
        // 点击遮罩才关闭（点面板内部不关）
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        id={panelId}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-5xl rounded-2xl border border-line bg-surface-panel shadow-xl"
      >
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <div>
            <h2 id={titleId} className="text-sm font-semibold text-ink">
              创作工坊
            </h2>
            <p className="text-xs text-ink-soft">
              自定义陪伴角色的性格、背景故事，以及按条件触发的世界设定
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={close}
            className="focus-ring rounded-lg border border-line px-3 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover"
          >
            关闭
          </button>
        </header>

        <div
          role="tablist"
          aria-label="创作工坊分区"
          className="flex gap-1 border-b border-line px-4 py-2"
        >
          {TABS.map((item) => {
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`${panelId}-tab-${item.id}`}
                aria-selected={active}
                aria-controls={tabPanelId(item.id)}
                onClick={() => setTab(item.id)}
                className={`focus-ring rounded-lg px-3 py-1.5 text-xs transition-colors ${
                  active
                    ? "bg-accent-soft text-accent-text"
                    : "text-ink-muted hover:bg-surface-hover"
                }`}
              >
                {item.label}
                <span className="ml-2 text-[11px] text-ink-faint">{item.hint}</span>
              </button>
            );
          })}
        </div>

        <div className="max-h-[72vh] overflow-y-auto p-5">
          {loading && !catalog && <p className="text-sm text-ink-soft">读取中…</p>}

          {error && (
            <div
              role="alert"
              className="mb-3 flex items-center gap-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger-text"
            >
              <span>{error}</span>
              <button
                type="button"
                onClick={() => void load()}
                className="focus-ring rounded-lg border border-danger/40 px-2 py-0.5"
              >
                重试
              </button>
            </div>
          )}

          {catalog && (
            <>
              {/* 用户手改坏的 YAML 会被后端跳过：这里必须让用户看见，否则会误以为「内容丢了」 */}
              {catalog.warnings.length > 0 && (
                <div
                  role="status"
                  className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning-text"
                >
                  <p className="font-medium">有文件未能读取（已跳过，不影响其它内容）：</p>
                  <ul className="mt-1 list-disc pl-4">
                    {catalog.warnings.map((warning) => (
                      <li key={warning} className="break-words">
                        {warning}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div
                role="tabpanel"
                id={tabPanelId(tab)}
                aria-labelledby={`${panelId}-tab-${tab}`}
              >
                {tab === "persona" ? (
                  <PersonaStudio
                    catalog={catalog}
                    disabled={disabled}
                    onCatalog={applyCatalog}
                  />
                ) : (
                  <WorldBookStudio
                    catalog={catalog}
                    disabled={disabled}
                    defaultScope={defaultScope}
                    onCatalog={applyCatalog}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
