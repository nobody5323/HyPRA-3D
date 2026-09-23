"use client";

/**
 * 对话模型设置：在页面上切换 LLM（OpenAI 兼容 API），无需改 .env 或重启后端。
 *
 * 与「预设档」的分工：**这里选用哪个模型**；预设档（PresetSwitcher）配的是
 * **这个模型的采样参数**——切换模型后，预设档的「自动」会按新模型名重新匹配。
 *
 * 安全：api_key **只单向提交**（后端存本机运行时文件，已 gitignore）。
 * 面板拿不到明文，因此留空 = 沿用已保存的 key；placeholder 会说明这一点。
 *
 * 无障碍（延续 AvatarSettings 的约定）：模态对话框 + 打开即聚焦 + Esc 关闭并归还焦点；
 * 结果用 role="status"，失败用 role="alert"。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  getLlmConfig,
  listLlmModels,
  resetLlmConfig,
  testLlmConfig,
  updateLlmConfig,
} from "@/lib/api/client";
import type { LlmConfigInfo, LlmConfigInput, LlmConfigResponse } from "@/lib/api/types";

const SOURCE_LABEL: Record<string, { text: string; className: string }> = {
  runtime: { text: "界面设置（已存本机）", className: "bg-success-soft text-success-text" },
  env: { text: "来自部署配置 .env", className: "bg-accent-soft text-accent-text" },
  session: { text: "本次运行生效（未保存）", className: "bg-warning-soft text-warning-text" },
};

/** 输入框通用样式 */
const FIELD_CLASS =
  "focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent";

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

export function LlmSettings({
  open,
  onClose,
  onApplied,
  panelId = "llm-settings",
}: {
  open: boolean;
  onClose: () => void;
  /** 应用 / 恢复成功后回调（页面据此刷新顶部显示与「自动」预设档） */
  onApplied?: (config: LlmConfigInfo) => void;
  panelId?: string;
}) {
  const [catalog, setCatalog] = useState<LlmConfigResponse | null>(null);
  const [provider, setProvider] = useState("mock");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [timeoutValue, setTimeoutValue] = useState("");
  const [thinking, setThinking] = useState<"inherit" | "on" | "off">("inherit");
  const [persist, setPersist] = useState(true);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const firstFieldRef = useRef<HTMLSelectElement>(null);
  /** 打开面板前持有焦点的元素：关闭时把焦点还回去 */
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = `${panelId}-title`;

  const current = catalog?.config ?? null;

  /** 把后端配置回填到表单（api_key 不回填：明文只存在后端） */
  const fillFrom = useCallback((config: LlmConfigInfo) => {
    setProvider(config.provider);
    setBaseUrl(config.base_url);
    setModel(config.model);
    setTimeoutValue(String(config.timeout));
    setThinking(
      config.enable_thinking === null ? "inherit" : config.enable_thinking ? "on" : "off",
    );
    setApiKey("");
    setModels([]);
  }, []);

  function close() {
    onClose();
    const previous = restoreFocusRef.current;
    restoreFocusRef.current = null;
    if (previous && previous.isConnected) previous.focus();
  }

  // 打开时拉取当前配置并回填
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setStatus(null);
    setError(null);
    void getLlmConfig().then((data) => {
      if (cancelled) return;
      setCatalog(data);
      if (data) fillFrom(data.config);
      else setError("后端未连接，读不到当前模型配置。");
    });
    return () => {
      cancelled = true;
    };
  }, [open, fillFrom]);

  // 打开后聚焦首个控件，并记住来源焦点
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    firstFieldRef.current?.focus();
  }, [open]);

  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const providerOptions = catalog?.providers ?? [];
  const providerHint = providerOptions.find((item) => item.value === provider)?.hint ?? "";

  function buildInput(): LlmConfigInput {
    return {
      provider,
      base_url: baseUrl,
      model,
      // 留空 = 沿用已保存的 key（前端拿不到明文）
      api_key: apiKey.trim() === "" ? null : apiKey.trim(),
      timeout: timeoutValue.trim() === "" ? null : Number(timeoutValue),
      enable_thinking: thinking === "inherit" ? null : thinking === "on",
      persist,
    };
  }

  function handleProviderChange(value: string) {
    setProvider(value);
    // 切 provider 时填入该家的默认端点（用户可再改）
    setBaseUrl(catalog?.default_base_urls?.[value] ?? "");
    setModels([]);
    setStatus(null);
  }

  async function handleLoadModels() {
    setLoadingModels(true);
    setError(null);
    setStatus(null);
    try {
      const list = await listLlmModels({ baseUrl, apiKey, provider });
      setModels(list);
      setStatus(
        list.length > 0
          ? `拉到 ${list.length} 个模型，可在「模型」一栏下拉选择`
          : "端点没有返回模型列表，手动填写模型名即可",
      );
    } catch (err) {
      setError(errorText(err, "拉取模型列表失败"));
    } finally {
      setLoadingModels(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setError(null);
    setStatus(null);
    try {
      const result = await testLlmConfig(buildInput());
      if (result.ok) {
        setStatus(`连接成功（${result.latency_ms} ms，模型 ${result.model}）`);
      } else {
        setError(result.error || "连接失败");
      }
    } catch (err) {
      setError(errorText(err, "测试连接失败"));
    } finally {
      setTesting(false);
    }
  }

  async function handleApply() {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const config = await updateLlmConfig(buildInput());
      setCatalog((prev) => (prev ? { ...prev, config } : prev));
      setStatus(
        `已切换为 ${config.model || config.provider}${
          config.source === "session" ? "（本次运行生效，未写入本机）" : ""
        }`,
      );
      setApiKey("");
      onApplied?.(config);
    } catch (err) {
      setError(errorText(err, "切换模型失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const config = await resetLlmConfig();
      setCatalog((prev) => (prev ? { ...prev, config } : prev));
      fillFrom(config);
      setStatus("已恢复为部署配置（.env）");
      onApplied?.(config);
    } catch (err) {
      setError(errorText(err, "恢复部署配置失败"));
    } finally {
      setBusy(false);
    }
  }

  const badge = SOURCE_LABEL[current?.source ?? "env"] ?? SOURCE_LABEL.env;

  return (
    <div
      id={panelId}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="absolute right-0 top-full z-30 mt-2 max-h-[min(80vh,620px)] w-[420px] overflow-y-auto rounded-2xl border border-line bg-surface-panel p-4 shadow-lg"
    >
      <div className="flex items-start justify-between">
        <div>
          <h2 id={titleId} className="text-sm font-medium text-ink">
            对话模型
          </h2>
          <p className="mt-0.5 text-xs text-ink-soft">
            走 OpenAI 兼容 API，切换后下一轮对话即生效
          </p>
        </div>
        <button
          type="button"
          onClick={close}
          className="focus-ring rounded-lg px-2 py-1 text-xs text-ink-soft transition-colors hover:bg-surface-hover hover:text-ink"
        >
          关闭
        </button>
      </div>

      {current && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2.5 py-1 text-xs ${badge.className}`}>
            {badge.text}
          </span>
          <span className="text-xs text-ink-muted">
            当前：{current.model || "—"}
            {current.has_api_key ? "" : "（未配置 Key）"}
          </span>
        </div>
      )}

      <div className="mt-3 space-y-3">
        <div className="block">
          <label className="block">
            <span className="text-xs text-ink-muted">服务商</span>
            <select
              ref={firstFieldRef}
              value={provider}
              onChange={(event) => handleProviderChange(event.target.value)}
              className={FIELD_CLASS}
            >
              {providerOptions.length === 0 && <option value={provider}>{provider}</option>}
              {providerOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {/* 说明文本放在 label 之外：label 的文本必须只是「服务商」，
              否则读屏与 getByLabelText 会拿到一串混合文本 */}
          {providerHint && <p className="mt-1 text-xs text-ink-faint">{providerHint}</p>}
        </div>

        <label className="block">
          <span className="text-xs text-ink-muted">Base URL（端点）</span>
          <input
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://api.example.com/v1"
            autoComplete="off"
            spellCheck={false}
            className={FIELD_CLASS}
          />
        </label>

        <label className="block">
          <span className="text-xs text-ink-muted">API Key</span>
          <input
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            type="password"
            autoComplete="off"
            placeholder={
              current?.has_api_key ? "已保存，留空则沿用" : "粘贴你的 API Key（只存本机后端）"
            }
            className={FIELD_CLASS}
          />
        </label>

        <label className="block">
          <span className="text-xs text-ink-muted">模型</span>
          <div className="mt-1 flex gap-2">
            {/* 用 datalist 而非 select：模型可能有几百个，原生输入框 + 建议更实用 */}
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              list={`${panelId}-models`}
              placeholder="例如 qwen-plus / Qwen/Qwen3-8B"
              autoComplete="off"
              spellCheck={false}
              className="focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent"
            />
            <datalist id={`${panelId}-models`}>
              {models.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <button
              type="button"
              onClick={() => void handleLoadModels()}
              disabled={loadingModels || provider === "mock"}
              className="focus-ring shrink-0 rounded-lg border border-line px-3 text-xs text-ink-soft transition-colors hover:bg-surface-hover disabled:opacity-40"
            >
              {loadingModels ? "拉取中…" : "拉取列表"}
            </button>
          </div>
        </label>

        <div className="flex flex-wrap gap-3">
          <label className="block w-28">
            <span className="text-xs text-ink-muted">超时（秒）</span>
            <input
              value={timeoutValue}
              onChange={(event) => setTimeoutValue(event.target.value)}
              inputMode="decimal"
              className="focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink focus:border-accent"
            />
          </label>
          <label className="block flex-1">
            <span className="text-xs text-ink-muted">思考链</span>
            <select
              value={thinking}
              onChange={(event) =>
                setThinking(event.target.value as "inherit" | "on" | "off")
              }
              className="focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink focus:border-accent"
            >
              <option value="inherit">不指定（交给模型/预设档）</option>
              <option value="off">关闭（推理模型提速）</option>
              <option value="on">开启</option>
            </select>
          </label>
        </div>

        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={persist}
            onChange={(event) => setPersist(event.target.checked)}
            className="focus-ring accent-accent"
          />
          保存到本机（重启后端后仍生效）
        </label>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void handleApply()}
          disabled={busy}
          className="focus-ring flex-1 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
        >
          {busy ? "应用中…" : "应用"}
        </button>
        <button
          type="button"
          onClick={() => void handleTest()}
          disabled={testing || busy}
          className="focus-ring rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          {testing ? "测试中…" : "测试连接"}
        </button>
        <button
          type="button"
          onClick={() => void handleReset()}
          disabled={busy}
          className="focus-ring rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          恢复部署配置
        </button>
      </div>

      {status && (
        <p role="status" className="mt-2 text-xs text-success-text">
          {status}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs leading-relaxed text-danger-text">
          {error}
        </p>
      )}

      <div className="mt-3 space-y-1 border-t border-line pt-3 text-xs leading-relaxed text-ink-soft">
        <p>
          Key 只保存在<b className="font-medium text-ink-muted">本机后端</b>的运行时配置里
          （已 gitignore），接口不会回传明文，也不会写进日志。
        </p>
        <p>
          「预设档」与这里是两件事：预设档配的是<b className="font-medium text-ink-muted">采样参数</b>
          （温度、长度上限），切换模型后它会按新模型名重新匹配。
        </p>
      </div>
    </div>
  );
}
