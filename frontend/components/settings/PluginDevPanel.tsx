"use client";

/**
 * 插件开发 / 接入面板（能力中心里「添加插件」展开的那块，`AGENTS.md §9.3`）。
 *
 * 定位：插件由**用户自己写**（宿主不再用模型生成插件代码——生成即执行等于把
 * 「自己审阅」这一步挤掉）。所以这块面板要回答三个问题：
 *
 * 1. 插件长什么样、写到哪 —— 指向 `docs/plugin-development.md` 与可复制的
 *    `docs/examples/plugin-hello/`；
 * 2. 写在别处的插件怎么接进来 —— **零拷贝**（`PLUGIN_EXTRA_DIRS` 指向插件所在目录，
 *    开发期最顺手，宿主不碰源文件）或**导入**（复制进用户插件目录）；
 * 3. 接进来之后在哪启用 —— 就在面板下面的列表里（本面板只管「发现」）。
 *
 * 安全口径（不许淡化）：插件代码跑在宿主进程内、**没有沙箱**。导入 = 信任这份代码，
 * 与手动 `pip install` 一个包是同一性质的风险；界面不得出现「官方认证」「安全插件」
 * 之类暗示担保的措辞（`docs/plugin-market.md §5.3`）。
 *
 * 目录信息按需拉取（`GET /plugins`）：列表本身页面已从 `/health` 轮询拿到，
 * 这里只在展开时才问「插件放哪儿」，不参与轮询。
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, getPlugins, importPlugin, reloadPlugins } from "@/lib/api/client";
import type { PluginDirs, PluginStatus } from "@/lib/api/types";

const FIELD_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

const HINT_CLASS = "text-[11px] leading-relaxed text-ink-faint";

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

/** 一行「路径 + 说明」；路径用等宽字体，方便用户照着去找/去填。 */
function PathLine({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className={HINT_CLASS}>{label}</span>
      <code className="break-all rounded bg-surface-inset px-2 py-1 font-mono text-[11px] text-ink-muted">
        {value}
      </code>
      {hint && <span className={HINT_CLASS}>{hint}</span>}
    </div>
  );
}

export function PluginDevPanel({
  onPluginsChanged,
}: {
  /** 导入 / 重扫后回传最新插件列表，页面据此更新列表与统计 */
  onPluginsChanged?: (plugins: PluginStatus[]) => void;
}) {
  const [dirs, setDirs] = useState<PluginDirs | null>(null);
  const [sourcePath, setSourcePath] = useState("");
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState<"import" | "reload" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadDirs = useCallback(async () => {
    try {
      const result = await getPlugins();
      setDirs(result.dirs);
    } catch {
      // 目录信息拿不到不该让整块面板消失：导入 / 重扫两个动作仍然可用
      setDirs(null);
    }
  }, []);

  useEffect(() => {
    void loadDirs();
  }, [loadDirs]);

  async function handleImport() {
    const path = sourcePath.trim();
    if (!path) return;
    setBusy("import");
    setError(null);
    setNotice(null);
    try {
      const result = await importPlugin({ path, replace });
      onPluginsChanged?.(result.plugins);
      setNotice(`已导入「${result.id}」到 ${result.path}。在下面的列表里启用它。`);
      setSourcePath("");
      setReplace(false);
    } catch (err) {
      setError(errorText(err, "导入失败"));
    } finally {
      setBusy(null);
    }
  }

  async function handleReload() {
    setBusy("reload");
    setError(null);
    setNotice(null);
    try {
      const result = await reloadPlugins();
      onPluginsChanged?.(result.plugins);
      setNotice(
        result.discovered.length > 0
          ? `发现 ${result.discovered.length} 个插件：${result.discovered.join("、")}`
          : "没有发现新插件（目录里的插件都已登记）",
      );
      // 目录可能刚被改过（用户加了 PLUGIN_EXTRA_DIRS 后重启），顺手刷新一次
      void loadDirs();
    } catch (err) {
      setError(errorText(err, "重新扫描失败"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-2 flex flex-col gap-3 card p-3">
      <p className={`rounded-lg bg-warning-soft px-3 py-2 ${HINT_CLASS} text-warning-text`}>
        <strong>插件跑在宿主进程里，没有沙箱。</strong>
        导入或启用一份插件 = 信任这份代码，与手动装一个依赖包是同一性质的风险。
        代码由你自己写、自己审阅——宿主不会替你生成插件代码。
      </p>

      <div className="flex flex-col gap-1">
        <p className="text-xs text-ink-muted">怎么写</p>
        <p className={HINT_CLASS}>
          在任意目录建一个插件目录：<code className="font-mono">manifest.json</code>（声明）
          + <code className="font-mono">plugin.py</code>（入口，必须有{" "}
          <code className="font-mono">def build(ctx)</code>）。完整接口见仓库里的{" "}
          <code className="font-mono">docs/plugin-development.md</code>，可直接复制{" "}
          <code className="font-mono">docs/examples/plugin-hello/</code> 改。
        </p>
      </div>

      <div className="flex flex-col gap-1">
        <p className="text-xs text-ink-muted">怎么接进来（二选一）</p>
        <ol className={`list-decimal pl-4 ${HINT_CLASS}`}>
          <li>
            <strong>零拷贝（开发期推荐）</strong>：把插件所在目录的父目录填进{" "}
            <code className="font-mono">PLUGIN_EXTRA_DIRS</code>（逗号分隔，见{" "}
            <code className="font-mono">.env.example</code>），重启后点「重新扫描」——
            宿主直接扫你的目录，改完代码不用复制。
          </li>
          <li>
            <strong>导入</strong>：在下面填插件目录路径，宿主把它复制到用户插件目录。
            适合「拿到一份插件包，想装进程序里」。
          </li>
        </ol>
      </div>

      {dirs && (
        <div className="flex flex-col gap-2">
          <PathLine
            label="用户插件目录（把插件目录丢进来，再点「重新扫描」）"
            value={dirs.user_dir}
          />
          <PathLine
            label={
              dirs.extra_dirs.length > 0
                ? "额外来源目录（PLUGIN_EXTRA_DIRS，零拷贝）"
                : "额外来源目录（PLUGIN_EXTRA_DIRS，当前未配置）"
            }
            value={dirs.extra_dirs.length > 0 ? dirs.extra_dirs.join("，") : "（空）"}
          />
        </div>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-xs text-ink-muted">
          插件目录路径 <span className="text-ink-faint">（其下须有 manifest.json）</span>
        </span>
        <input
          value={sourcePath}
          disabled={busy !== null}
          onChange={(event) => setSourcePath(event.target.value)}
          placeholder="例如 D:/my-plugins/water-tracker"
          className={FIELD_CLASS}
        />
      </label>

      <label className="flex items-center gap-2 text-[11px] text-ink-muted">
        <input
          type="checkbox"
          checked={replace}
          disabled={busy !== null}
          onChange={(event) => setReplace(event.target.checked)}
        />
        目标已存在时整体替换（默认拒绝，避免覆盖你已装好的插件）
      </label>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy !== null || !sourcePath.trim()}
          onClick={() => void handleImport()}
          className="focus-ring rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
        >
          {busy === "import" ? "导入中…" : "导入插件"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void handleReload()}
          className="focus-ring rounded-lg border border-line px-3 py-1.5 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          {busy === "reload" ? "扫描中…" : "重新扫描"}
        </button>
      </div>

      {error && (
        <p role="alert" className="text-[11px] leading-relaxed text-danger-text">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-[11px] leading-relaxed text-success-text">
          {notice}
        </p>
      )}
    </div>
  );
}
