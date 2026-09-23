"use client";

/**
 * 插件详情与操作区：**启停** + **配置**（`AGENTS.md §9.5`）。
 *
 * 配置只在展开详情时才拉（`GET /plugins/{id}/settings`）——插件数量不多，但也没必要
 * 一打开页面就为每个插件各发一次请求。
 *
 * 配置表单由 `SchemaForm` 按插件的 `settings_schema` 渲染，插件不自带前端代码。
 * 权限声明同时摊给用户看：§9.3 说「权限是架构约束，不是口头承诺」，
 * 那它就不该只写在 manifest 里——界面必须让人看得见边界。
 */

import { useEffect, useState } from "react";

import { SchemaForm, missingRequired } from "@/components/settings/SchemaForm";
import {
  ApiError,
  getPluginSettings,
  putPluginSettings,
  setPluginEnabled,
} from "@/lib/api/client";
import type {
  PluginSettingsResponse,
  PluginSettingsSchema,
  PluginStatus,
} from "@/lib/api/types";

const HINT_CLASS = "text-[11px] leading-relaxed text-ink-faint";
const BUTTON_CLASS =
  "focus-ring rounded-lg border border-line bg-surface-raised px-2.5 py-1 text-[11px] text-ink hover:border-accent disabled:opacity-50";

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

/**
 * 把 schema 的 `default` 合并进已保存的值。
 *
 * 界面显示什么，保存时写进去的就该是什么——否则用户看到一个"已经填好"的目录，
 * 不动它直接保存，后端却拿到空配置。
 */
function withDefaults(
  values: Record<string, unknown>,
  schema: PluginSettingsSchema,
): Record<string, unknown> {
  const next = { ...values };
  for (const [key, field] of Object.entries(schema.properties ?? {})) {
    if (!(key in next) && field.default !== undefined) next[key] = field.default;
  }
  return next;
}

export function PluginDetail({
  plugin,
  onPluginsChanged,
}: {
  plugin: PluginStatus;
  /** 启停 / 保存后回传最新快照，页面据此就地更新列表（不必再轮询一次 /health） */
  onPluginsChanged?: (plugins: PluginStatus[]) => void;
}) {
  const [settings, setSettings] = useState<PluginSettingsResponse | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setStatus(null);

    getPluginSettings(plugin.id)
      .then((next) => {
        if (cancelled) return;
        setSettings(next);
        setValues(withDefaults(next.values, next.schema));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errorText(err, "读取插件配置失败"));
        setSettings(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [plugin.id]);

  async function handleToggle(enabled: boolean) {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await setPluginEnabled(plugin.id, enabled);
      onPluginsChanged?.(result.plugins);
      setStatus(enabled ? "已启用，本次运行即刻生效。" : "已禁用，本次运行即刻停止。");
    } catch (err) {
      setError(errorText(err, "切换插件状态失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    if (!settings) return;

    // 必填校验在提交前做：后端不校验 schema 的 required（插件自己也不该重复实现）
    const missing = missingRequired(settings.schema, values);
    if (missing.length > 0) {
      setStatus(null);
      setError(`还有必填项未填：${missing.join("、")}`);
      return;
    }

    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await putPluginSettings(plugin.id, values);
      onPluginsChanged?.(result.plugins);
      setStatus("配置已保存并生效。");
    } catch (err) {
      setError(errorText(err, "保存插件配置失败"));
    } finally {
      setBusy(false);
    }
  }

  const schema = settings?.schema;
  const hasSettings = Boolean(schema && Object.keys(schema.properties ?? {}).length > 0);
  const isCore = plugin.layer === "core";
  const coreNoteId = `plugin-${plugin.id}-core-note`;

  return (
    <div className="mt-1.5 flex flex-col gap-2 rounded-lg border border-line bg-surface-inset p-2.5">
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="focus-ring mt-0.5 h-4 w-4 rounded border-line accent-accent"
          checked={plugin.enabled}
          disabled={busy || isCore}
          aria-describedby={isCore ? coreNoteId : undefined}
          onChange={(event) => void handleToggle(event.target.checked)}
        />
        <span className="text-[11px] text-ink-muted">
          {plugin.enabled ? "已启用" : "已禁用"}
          {isCore && (
            <span id={coreNoteId} className="ml-1 text-ink-faint">
              （core 层不可禁用：换掉它产品就不成立）
            </span>
          )}
        </span>
      </label>

      {loading && <p className={HINT_CLASS}>读取配置中…</p>}

      {settings && (
        <p className={HINT_CLASS}>
          权限：文件系统
          {settings.permissions.filesystem.write ? "可写" : "只读"}
          {settings.permissions.filesystem.read.length > 0 &&
            `（可读 ${settings.permissions.filesystem.read.join("、")}）`}
          ；网络
          {settings.permissions.network.hosts.length > 0
            ? `白名单 ${settings.permissions.network.hosts.join("、")}`
            : "不访问"}
        </p>
      )}

      {hasSettings && schema && (
        <>
          <SchemaForm
            schema={schema}
            values={values}
            onChange={setValues}
            disabled={busy}
            idPrefix={`plugin-${plugin.id}`}
          />
          <div>
            <button type="button" className={BUTTON_CLASS} disabled={busy} onClick={() => void handleSave()}>
              {busy ? "处理中…" : "保存配置"}
            </button>
          </div>
        </>
      )}

      {!loading && settings && !hasSettings && <p className={HINT_CLASS}>此插件没有可配置项。</p>}

      {plugin.error && (
        <p role="alert" className="text-[11px] text-danger-text">
          运行错误：{plugin.error}
        </p>
      )}

      {status && (
        <p role="status" className="text-[11px] text-success-text">
          {status}
        </p>
      )}

      {error && (
        <p role="alert" className="text-[11px] text-danger-text">
          {error}
        </p>
      )}
    </div>
  );
}
