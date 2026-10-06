"use client";

/**
 * 酒馆会话 → 长期记忆的导入面板（`AGENTS.md` P4 的界面入口）。
 *
 * 为什么独立成一块，而不是塞进 tavern-bridge 插件的配置表单里：
 * 「对不上角色卡的会话归到哪个陪伴对象」是一次**选择**，不是插件配置。
 * 同一份插件配置下，不同陪伴对象的记忆是**隔离**的（§8.2 的 `companion:{id}`）；
 * 塞进 settings 会误导用户以为「填一次就通用」。
 *
 * 注：酒馆里**不同角色的剧情各进各的陪伴对象**（宿主按角色分开写），
 * 这里的 `companionId` 只是「角色卡被删了 / 对不上」时的回落去处。
 *
 * **面板自带陪伴对象选择器**：`companionId` 只是初始值，用户在面板里可以改。
 * 这一点是硬要求而不是锦上添花——宿主只要把 id 传错（控制台曾经把数字人模型 id
 * 当成 companionId 传进来，值恒为空串），面板就会退化成一个点不动的死界面；
 * 面板能自己选，就永远不会卡在这一步。
 *
 * 未就绪（409 / 404）按**预期状态**渲染指路文案，不弹错误框：
 * 用户的第一步本来就该是「先启用插件、填目录」，这不是失败。
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getPersonas,
  getPluginSettings,
  getTavernBridgeStatus,
  getTavernMountedBooks,
  importTavernMemory,
  putPluginSettings,
  setTavernMountedBooks,
  syncTavernKnowledge,
} from "@/lib/api/client";
import type {
  PersonaCatalog,
  TavernBridgeStatus,
  TavernImportResult,
  TavernKnowledgeSyncResult,
  TavernMountedBooks,
} from "@/lib/api/types";

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

/**
 * 把来源标识（`world/<文件名>` / `char/<角色名>`）转成挂载列表里的展示名。
 *
 * **不带 `world/` `char/` 前缀**：作用域里已经区分了两类来源，
 * 而界面这里用户关心的是书名本身（「内嵌」二字足以说明它来自角色卡）。
 * 前缀留在挂载列表的标题与无障碍名里（见复选框的 `aria-label`），
 * 不与「参与接入的世界书」那组勾选框撞名。
 */
function formatMountLabel(source: string): string {
  const slash = source.indexOf("/");
  const kind = slash < 0 ? "" : source.slice(0, slash);
  const name = slash < 0 ? source : source.slice(slash + 1);
  return kind === "char" && name ? `内嵌·${name}` : name || source;
}

export function TavernImportPanel({
  companionId,
  personas: personasProp = null,
  onCompanionChange,
}: {
  companionId: string;
  /**
   * 陪伴对象清单（可选）。
   *
   * 不传时本面板自己拉一份——面板必须能独立回答「导入到哪个陪伴对象」，
   * 否则宿主一旦忘了传（曾经真的发生过：控制台把数字人模型 id 当成
   * companionId 传进来），用户看到的就是一块点不动的死界面。
   */
  personas?: PersonaCatalog | null;
  /** 选中的陪伴对象变化时回调（宿主可据此同步自己的选择器） */
  onCompanionChange?: (id: string) => void;
}) {
  const [status, setStatus] = useState<TavernBridgeStatus | null>(null);
  /** 未就绪的指路文案（插件未启用 / 未安装）；与 `error` 区分：那是真故障 */
  const [blocked, setBlocked] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /**
   * 面板内的导入目标。
   *
   * 与 `companionId` 分开存：宿主给的是**初始值**，用户在面板里改过之后
   * 以面板为准（改回时靠下面的 effect 跟随宿主）。
   */
  const [target, setTarget] = useState(companionId);
  /** 自拉的陪伴对象清单（宿主给了 `personas` 就不用它） */
  const [catalog, setCatalog] = useState<PersonaCatalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TavernImportResult | null>(null);
  /** 知识库同步是**另一条路**（按需召回），与记忆同步分开反馈 */
  const [knowledgeBusy, setKnowledgeBusy] = useState(false);
  const [knowledgeResult, setKnowledgeResult] =
    useState<TavernKnowledgeSyncResult | null>(null);
  /** 勾选某个来源时的保存中标记 */
  const [booksBusy, setBooksBusy] = useState(false);
  /**
   * 世界书挂载清单（酒馆模式下**只查挂上的那几本**）。
   *
   * 与上面的 `books`（接入勾选）分开：`books` 管「要不要读进知识库」，
   * 这里管「读进来的哪几本允许被检索」。默认全不挂——不挂就不串味。
   */
  const [mounted, setMounted] = useState<TavernMountedBooks | null>(null);
  /** 挂载清单的保存中标记（与 `booksBusy` 分开：两者是不同接口） */
  const [mountedBusy, setMountedBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 强制重导的二次确认：重新导入会重复写入记忆，不能一键直达 */
  const [forceArmed, setForceArmed] = useState(false);

  // 清单：宿主给了就用宿主的，否则自己拉一份（面板独立可用）
  useEffect(() => {
    if (personasProp) return;
    let active = true;
    void getPersonas().then((next) => {
      if (active) setCatalog(next);
    });
    return () => {
      active = false;
    };
  }, [personasProp]);

  // 宿主的选中项变化时跟随（例如用户在页面上换了陪伴对象）
  useEffect(() => {
    setTarget(companionId);
  }, [companionId]);

  const personas = personasProp?.personas ?? catalog?.personas ?? [];
  /**
   * 实际使用的陪伴对象。
   *
   * 只有一个陪伴对象时直接采用它：那时「没选」只是宿主还没把清单读回来，
   * 不是真的要用户做选择——为此卡住整块面板（曾经的行为）纯属自找麻烦。
   */
  const activeCompanionId = target || (personas.length === 1 ? personas[0].id : "");

  const load = useCallback(async () => {
    if (!activeCompanionId) {
      // 清单还没回来 / 确实有多个待选：先不发请求，界面会给出选择入口
      setStatus(null);
      setBlocked(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      // 状态与挂载清单一起拉：后者是独立端点（挂载与接入是两回事），
      // 但它读不出来不该拖垮整个面板——所以单独 catch、降级为 null。
      const [next] = await Promise.all([
        getTavernBridgeStatus(activeCompanionId),
        getTavernMountedBooks()
          .then(setMounted)
          .catch(() => setMounted(null)),
      ]);
      setStatus(next);
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
  }, [activeCompanionId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleImport(force: boolean) {
    setBusy(true);
    setError(null);
    setForceArmed(false);
    try {
      const next = await importTavernMemory(activeCompanionId, force);
      setResult(next);
      await load(); // 刷新「已导入」计数，否则进度条不动会让人以为没生效
    } catch (err) {
      setError(errorText(err, "导入失败"));
    } finally {
      setBusy(false);
    }
  }

  /** 面板内切换导入目标：先本地生效，再通知宿主（宿主可据此同步它自己的选择器） */
  function handleCompanionChange(id: string) {
    setTarget(id);
    setResult(null);
    setKnowledgeResult(null);
    onCompanionChange?.(id);
  }

  async function handleKnowledgeSync() {
    setKnowledgeBusy(true);
    setError(null);
    try {
      setKnowledgeResult(await syncTavernKnowledge());
      await load(); // 刷新文档计数，否则按钮点完看不到变化
    } catch (err) {
      setError(errorText(err, "同步知识库失败"));
    } finally {
      setKnowledgeBusy(false);
    }
  }

  /**
   * 就地应用一次勾选变化（不重拉状态接口）。
   *
   * 为什么不能 `await load()`：status 是重接口（读世界书 / 角色卡 / 会话，
   * 再逐个作用域查知识库），一次十秒上下；而勾一下世界书只是本地状态变了，
   * 用户要的是「勾上了」，不是「整块面板重新转圈」。
   *
   * 受影响的两处都能从这次操作本身算出来：
   *   - `books[].enabled`：就是用户刚改的那一项；
   *   - `available.entries`：取消勾选少掉这本书的条目数，勾回来再加回去。
   */
  function applyBookToggle(source: string, nextEnabled: boolean) {
    setStatus((prev) => {
      if (!prev) return prev;
      const books = prev.books.map((book) =>
        book.source === source ? { ...book, enabled: nextEnabled } : book,
      );
      const delta = books.find((book) => book.source === source)?.entries ?? 0;
      const entries = (prev.available.entries ?? 0) + (nextEnabled ? delta : -delta);
      return {
        ...prev,
        books,
        available: { ...prev.available, entries: Math.max(0, entries) },
      };
    });
  }

  async function handleToggleBook(source: string, nextEnabled: boolean) {
    setBooksBusy(true);
    setError(null);
    // 先就地生效：勾选框是受控组件，等网络往返回来才改状态的话，
    // 点下去会有小半秒「弹回去又跳过来」的闪烁
    const rollback = status;
    applyBookToggle(source, nextEnabled);
    try {
      // 读回当前配置再合并：配置里还有 tavern_dir 等键，
      // 只提交一个键会把其余的抹掉（后端 PUT 是「提交即全量」语义）
      const view = await getPluginSettings("tavern-bridge");
      const disabled = new Set(
        (Array.isArray(view.values.tavern_disabled_books)
          ? view.values.tavern_disabled_books
          : []
        ).filter((item): item is string => typeof item === "string"),
      );
      if (nextEnabled) disabled.delete(source);
      else disabled.add(source);
      await putPluginSettings("tavern-bridge", {
        ...view.values,
        tavern_disabled_books: [...disabled].sort(),
      });
    } catch (err) {
      setError(errorText(err, "保存勾选失败"));
      // 保存失败：后端一字未改（PUT 是全量写），把界面退回改动前即可，
      // 不必为此再等一次重接口
      setStatus(rollback);
    } finally {
      setBooksBusy(false);
    }
  }

  /**
   * 切换某本世界书的挂载状态（酒馆模式下「查不查这本」）。
   *
   * 与 `handleToggleBook`（接入勾选）的关键差别：**挂载是即时生效的**——
   * 检索每轮现读清单，保存后下一轮就换书，不必重新同步、不必重启。
   * 所以这里不做增量重算，直接重拉一次清单（它是个轻接口，不像 status 那么贵）。
   */
  async function handleToggleMounted(source: string, nextMounted: boolean) {
    if (!mounted) return;
    const previous = mounted;
    const nextList = nextMounted
      ? [...mounted.mounted, source]
      : mounted.mounted.filter((item) => item !== source);
    setMountedBusy(true);
    setError(null);
    // 先就地生效：勾选框是受控组件，等往返回来才改会有「弹回去」的闪烁
    setMounted({
      ...mounted,
      mounted: nextList,
      available: mounted.available.map((item) =>
        item.source === source ? { ...item, mounted: nextMounted } : item,
      ),
    });
    try {
      setMounted(await setTavernMountedBooks(nextList));
    } catch (err) {
      setError(errorText(err, "保存世界书挂载失败"));
      setMounted(previous);
    } finally {
      setMountedBusy(false);
    }
  }

  /** 只挂一本（「切换世界书」的最常用操作：避免多本互相干扰） */
  async function handleMountOnly(source: string) {
    if (!mounted) return;
    const previous = mounted;
    setMountedBusy(true);
    setError(null);
    setMounted({
      ...mounted,
      mounted: [source],
      available: mounted.available.map((item) => ({
        ...item,
        mounted: item.source === source,
      })),
    });
    try {
      setMounted(await setTavernMountedBooks([source]));
    } catch (err) {
      setError(errorText(err, "保存世界书挂载失败"));
      setMounted(previous);
    } finally {
      setMountedBusy(false);
    }
  }

  /** 全部卸下（回到「一本都不查」的默认态） */
  async function handleUnmountAll() {
    if (!mounted) return;
    const previous = mounted;
    setMountedBusy(true);
    setError(null);
    setMounted({
      ...mounted,
      mounted: [],
      available: mounted.available.map((item) => ({ ...item, mounted: false })),
    });
    try {
      setMounted(await setTavernMountedBooks([]));
    } catch (err) {
      setError(errorText(err, "保存世界书挂载失败"));
      setMounted(previous);
    } finally {
      setMountedBusy(false);
    }
  }

  const importedCount = status?.imported_sessions.length ?? 0;
  /** 本次写进了几个陪伴对象的记忆（按角色分开时 > 1） */
  const scopeCount = result ? Object.keys(result.scopes).length : 0;
  const pendingTurns = status?.pending_turns ?? 0;
  const pendingSessions = status?.pending_sessions ?? 0;
  const knowledge = status?.knowledge ?? { documents: 0, chunks: 0, scopes: 0 };
  const available = status?.available ?? {};
  const books = status?.books ?? [];
  /**
   * 有没有可勾选的世界书来源。
   *
   * 判据必须是**不受勾选影响**的 `books`，不能用 `available.entries`：
   * 后者是筛选后的计数，把书全部取消勾选就变成 0——拿它当渲染条件，
   * 整个勾选区会连同每一本书一起消失，用户再也勾不回来。
   * 后端 `book_catalog()` 特意不受勾选影响就是为了这个
   * （见 `backend/plugins/tavern-bridge/plugin.py`），前端别再把它绕回去。
   */
  const hasBooks = books.length > 0;
  /** 酒馆世界书只走知识库按需召回，不再做每轮直注入 */
  const worldbookHint = hasBooks
    ? `酒馆世界书不会整本直接写入提示词。勾选来源并点击「同步到知识库」后，当前输入相关的片段才会按需召回；未命中的设定不占提示词预算。`
    : "";

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

      {/*
        导入目标选择器：面板自己就能选。
        之前这块依赖宿主传对 `companionId`——宿主传空时整个面板只剩一句
        「请先选择一个陪伴对象」，而面板里根本没有可选的入口，用户直接卡死。
      */}
      {personas.length >= 2 && (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <label className="text-[11px] text-ink-muted" htmlFor="tavern-import-companion">
            导入到
          </label>
          <select
            id="tavern-import-companion"
            value={activeCompanionId}
            disabled={loading || busy || booksBusy}
            onChange={(event) => handleCompanionChange(event.target.value)}
            className="focus-ring rounded-lg border border-line bg-surface-inset px-2 py-1 text-[11px] text-ink disabled:opacity-60"
          >
            {/* 还没选时给一个占位项，否则浏览器会默认显示第一项、看起来像已经选好了 */}
            {!activeCompanionId && <option value="">请选择陪伴对象</option>}
            {personas.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.name}
              </option>
            ))}
          </select>
          <span className="text-[11px] text-ink-faint">
            记忆按它隔离；对不上角色卡的会话归到这里
          </span>
        </div>
      )}

      {!activeCompanionId && personas.length === 0 && (
        <p className={HINT_CLASS}>
          还没有可用的陪伴对象：先去「创作工坊」建一个角色，再回来导入。
        </p>
      )}

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

          {worldbookHint && <p className={HINT_CLASS}>{worldbookHint}</p>}

          <p className="mt-1 text-[11px] text-ink-muted">
            当前陪伴对象已导入：<span className="tabular-nums">{importedCount}</span> 个会话
          </p>

          <p className={HINT_CLASS}>
            {pendingTurns > 0
              ? `有 ${pendingTurns} 轮新对话待同步（分布在 ${pendingSessions} 个会话里）——已同步过的不会重复写入。`
              : "记忆已是最新（酒馆里的新对话会在这里显示待同步轮数）。"}
          </p>

          <p className={HINT_CLASS}>
            酒馆里不同角色的剧情会各进各的陪伴对象：选哪个角色，就只召回那个角色的上下文。
            只有对不上角色卡的会话（卡被删了）才归到当前陪伴对象。
          </p>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="focus-ring rounded-lg border border-line bg-surface-raised px-2.5 py-1 text-[11px] text-ink hover:border-accent disabled:opacity-50"
              disabled={busy}
              onClick={() => void handleImport(false)}
            >
              {busy ? "同步中…" : "同步新对话"}
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
            已同步过的轮次会被跳过，因此重复点「同步新对话」是安全的；
            「重新导入」是从头重写（会重复写入记忆），只在修复数据或换了模型后需要。
          </p>

          {hasBooks && (
            <div className="mt-2 border-t border-line pt-2">
              <p className="text-[11px] text-ink-muted">参与接入的世界书</p>
              <ul className="mt-1 flex flex-col gap-0.5">
                {books.map((book) => (
                  <li key={book.source}>
                    <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-ink">
                      <input
                        type="checkbox"
                        className="focus-ring h-3.5 w-3.5 rounded border-line accent-accent"
                        checked={book.enabled}
                        disabled={booksBusy}
                        // 显式无障碍名，与下面挂载那组的 `酒馆模式挂载 …` 区分开：
                        // 同一本书两组勾选框，靠包裹文本取名会完全撞名
                        aria-label={`参与接入 ${book.source}`}
                        onChange={(event) =>
                          void handleToggleBook(book.source, event.target.checked)
                        }
                      />
                      <span className="truncate">{book.label}</span>
                      <span className="shrink-0 text-ink-faint">（{book.entries} 条）</span>
                    </label>
                  </li>
                ))}
              </ul>
              <p className={HINT_CLASS}>
                取消勾选的来源既不注入对话、也不同步进知识库；
                角色卡本身与它们的对话不受影响。
              </p>
            </div>
          )}

          {hasBooks && (
            <div className="mt-2 border-t border-line pt-2">
              <p className="text-[11px] text-ink-muted">
                世界书已入知识库：{knowledge.documents} 篇 / {knowledge.chunks} 条设定
                {knowledge.scopes > 1 ? `（${knowledge.scopes} 个作用域）` : ""}
              </p>
              <p className={HINT_CLASS}>
                这条是按需召回：问到相关的事才进上下文，不占每轮的注入预算。
                同步会把书全部读进库（随时可切），**挂哪几本才决定查哪几本** —— 见下面。
              </p>
              <button
                type="button"
                className="focus-ring mt-1 rounded-lg border border-line bg-surface-raised px-2.5 py-1 text-[11px] text-ink hover:border-accent disabled:opacity-50"
                disabled={knowledgeBusy}
                onClick={() => void handleKnowledgeSync()}
              >
                {knowledgeBusy ? "同步中…" : "同步到知识库"}
              </button>
              {knowledgeResult && (
                <p role="status" className="mt-1 text-[11px] leading-relaxed text-success-text">
                  已写入 {knowledgeResult.documents} 篇 / {knowledgeResult.chunks} 条设定
                  {knowledgeResult.removed > 0
                    ? `，清理 ${knowledgeResult.removed} 篇失效来源`
                    : ""}
                  {knowledgeResult.skipped > 0
                    ? `，跳过 ${knowledgeResult.skipped} 条找不到角色卡的设定`
                    : ""}
                  。
                </p>
              )}
            </div>
          )}

          {mounted && mounted.available.length > 0 && (
            <div className="mt-2 border-t border-line pt-2">
              <div className="flex items-baseline justify-between gap-2">
                <p className="text-[11px] text-ink-muted">
                  酒馆模式下的世界书
                  <span className="ml-1 text-ink-faint">
                    （已挂 {mounted.mounted.length} / {mounted.available.length} 本）
                  </span>
                </p>
                {mounted.mounted.length > 0 && (
                  <button
                    type="button"
                    className="focus-ring shrink-0 rounded px-1 text-[11px] text-ink-faint hover:text-ink-muted disabled:opacity-50"
                    disabled={mountedBusy}
                    onClick={() => void handleUnmountAll()}
                  >
                    全部卸下
                  </button>
                )}
              </div>
              <ul className="mt-1 flex flex-col gap-0.5">
                {mounted.available.map((item) => (
                  <li key={item.source} className="flex items-center gap-2">
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-[11px] text-ink">
                      <input
                        type="checkbox"
                        className="focus-ring h-3.5 w-3.5 shrink-0 rounded border-line accent-accent"
                        checked={item.mounted}
                        disabled={mountedBusy}
                        // 显式无障碍名：同一本书在上面「参与接入」那组里也有勾选框，
                        // 两组若都靠包裹文本取名，屏幕阅读器读起来完全一样
                        aria-label={`酒馆模式挂载 ${item.source}`}
                        onChange={(event) =>
                          void handleToggleMounted(item.source, event.target.checked)
                        }
                      />
                      <span className="truncate">{formatMountLabel(item.source)}</span>
                    </label>
                    {!item.mounted && (
                      <button
                        type="button"
                        className="focus-ring shrink-0 rounded px-1 text-[11px] text-ink-faint hover:text-accent disabled:opacity-50"
                        disabled={mountedBusy}
                        onClick={() => void handleMountOnly(item.source)}
                        title="只挂这一本（其余卸下）"
                      >
                        只挂这本
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              <p className={HINT_CLASS}>
                每本世界书各存各的，**只有挂上的才会被召回** —— 换书就是换挂载，
                立刻生效、不必重新同步。一本都不挂时，酒馆模式下不会用到任何世界书知识。
              </p>
            </div>
          )}

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
          {result.turns} 轮对话、{result.facts} 条事实、{result.memories} 条情景记忆
          {scopeCount > 1 ? `，写入 ${scopeCount} 个陪伴对象的记忆。` : "。"}
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
