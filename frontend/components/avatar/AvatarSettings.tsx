"use client";

/**
 * 数字人设置面板：**在页面上直接填写魔珐密钥**（无需改 .env 重新构建）。
 *
 * 凭证保存到 localStorage 并即时生效（SDK 会自动重建）；
 * 未配置时自动降级为「浏览器原生 TTS + 占位形象」。
 *
 * 组件是**受控的展示层**：凭证状态由页面统一持有（避免同一 store 被多处订阅）。
 *
 * 无障碍要点（审计 A3 / A8 / F1 / F2 / F3）：
 * - 面板是模态对话框（`role="dialog"` + `aria-modal` + `aria-labelledby`）；
 * - 打开即把焦点移入首个输入框，Esc 或「关闭」都会关闭面板并**把焦点归还触发器**；
 * - 保存 / 清除结果用 `role="status"` 播报；
 * - 面板自带最大高度与内部滚动，矮视口不会溢出遮挡页面内容。
 */

import { useEffect, useRef, useState } from "react";

import {
  AVATAR_RENDERER_OPTIONS,
  TTS_ENGINE_OPTIONS,
  type AvatarCredentials,
  type AvatarRendererPreference,
  type CredentialSource,
  type TtsEnginePreference,
} from "@/lib/avatar/avatar-config";
import type { AvatarModelsState } from "@/hooks/avatar/useAvatarModels";
import type { TtsVoicesStatus } from "@/lib/api/types";

import { ModelLibraryPanel } from "./ModelLibraryPanel";

const SOURCE_LABEL: Record<string, { text: string; className: string }> = {
  local: { text: "密钥已填写（本机保存）", className: "bg-success-soft text-success-text" },
  env: { text: "密钥来自部署配置", className: "bg-accent-soft text-accent-text" },
  none: { text: "未配置密钥 → 使用本地渲染器", className: "bg-warning-soft text-warning-text" },
};

export function AvatarSettings({
  open,
  onClose,
  credentials,
  source,
  save,
  clear,
  renderer,
  onRendererChange,
  ttsEngine,
  onTtsEngineChange,
  ttsVoice,
  onTtsVoiceChange,
  ttsStatus,
  onPreviewVoice,
  models,
  panelId = "avatar-settings",
}: {
  open: boolean;
  onClose: () => void;
  credentials: AvatarCredentials | null;
  source: CredentialSource;
  save: (credentials: AvatarCredentials) => boolean;
  clear: () => void;
  /** 当前渲染方式偏好 */
  renderer: AvatarRendererPreference;
  onRendererChange: (value: AvatarRendererPreference) => void;
  /** 当前语音引擎偏好（自动 / 强制服务端 / 强制浏览器） */
  ttsEngine: TtsEnginePreference;
  onTtsEngineChange: (value: TtsEnginePreference) => void;
  /** 选中的音色 id（空串 = 后端默认音色） */
  ttsVoice: string;
  onTtsVoiceChange: (voice: string) => void;
  /** 后端语音引擎状态（`GET /media/tts/voices`；null = 未探测到） */
  ttsStatus: TtsVoicesStatus | null;
  /** 试听指定音色（由页面注入，复用已测的服务端 TTS 控制器） */
  onPreviewVoice?: (voice: string) => Promise<void>;
  /** 模型库（清单 / 上传 / 选用 / 情绪指定） */
  models: AvatarModelsState;
  /** 面板 id（与触发器的 aria-controls 对应） */
  panelId?: string;
}) {
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [revealSecret, setRevealSecret] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  /** 试听进行中（按钮防重入：一句话没讲完不再叠一句） */
  const [previewing, setPreviewing] = useState(false);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  /** 打开面板前持有焦点的元素（通常是触发器）：关闭时把焦点还回去 */
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = `${panelId}-title`;

  /** 关闭面板并归还焦点（「关闭」按钮与 Esc 共用同一路径） */
  function close() {
    onClose();
    const previous = restoreFocusRef.current;
    restoreFocusRef.current = null;
    if (previous && previous.isConnected) previous.focus();
  }

  // 打开面板时同步当前值
  useEffect(() => {
    if (!open) return;
    setAppId(credentials?.appId ?? "");
    setAppSecret(credentials?.appSecret ?? "");
    setRevealSecret(false);
    setMessage(null);
  }, [open, credentials]);

  // 打开后把焦点移入首个输入框（键盘用户无需绕过整页 Tab），并记住来源焦点
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

  const canSave = appId.trim().length > 0 && appSecret.trim().length > 0;
  const badge = SOURCE_LABEL[source] ?? SOURCE_LABEL.none;

  /** 后端能出服务端音频吗（未探测到 / 未配好 → 只能用浏览器语音） */
  const serverTtsReady = Boolean(ttsStatus?.server_tts && ttsStatus.configured);
  /** 有音色可选时才展示音色区（只有 GPT-SoVITS 才有「音色」概念） */
  const voiceOptions = ttsStatus?.voices ?? [];
  const showVoices = serverTtsReady && (voiceOptions.length > 0 || Boolean(ttsStatus?.default_voice));
  /** 语音引擎状态说明：优先用后端给的 note，探测不到时自拟一句 */
  const ttsNote =
    ttsStatus?.note ||
    (ttsStatus
      ? serverTtsReady
        ? "服务端 TTS 已就绪（GPT-SoVITS）"
        : "后端未启用服务端 TTS"
      : "未探测到后端语音引擎（后端未就绪时会自动回落到浏览器语音）");

  function handleSave() {
    if (!canSave) return;
    const ok = save({ appId, appSecret });
    setMessage(
      ok
        ? "已保存，数字人将自动重新初始化。"
        : "保存失败：本机存储不可用（可能是隐私模式），请检查浏览器设置。",
    );
  }

  function handleClear() {
    clear();
    setAppId("");
    setAppSecret("");
    setMessage("已清除本机配置。");
  }

  /**
   * 试听当前音色。
   *
   * 走**页面注入的服务端 TTS 控制器**而不是在这里直接发请求：
   * 播报的打断/降级/超时/资源释放那套只应该有一份实现（已在 hook 里单测覆盖）。
   */
  async function handlePreview() {
    if (!onPreviewVoice || previewing) return;
    setPreviewing(true);
    try {
      await onPreviewVoice(ttsVoice);
    } catch {
      // 试听失败不应影响设置面板：仅回一句提示
      setMessage("试听失败，请确认后端 GPT-SoVITS 已启动。");
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <div
      id={panelId}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="absolute right-0 top-full z-30 mt-2 max-h-[min(70vh,520px)] w-[380px] overflow-y-auto rounded-2xl border border-line bg-surface-panel p-4 shadow-lg"
    >
      <div className="flex items-start justify-between">
        <div>
          <h2 id={titleId} className="text-sm font-medium text-ink">
            数字人设置
          </h2>
          <p className="mt-0.5 text-xs text-ink-soft">
            渲染方式与密钥均即时生效，无需重新构建前端
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

      <div className="mt-3">
        <span className={`inline-block rounded-full px-2.5 py-1 text-xs ${badge.className}`}>
          {badge.text}
        </span>
      </div>

      {/*
        渲染方式：三层降级链（魔珐 → Live2D → 静态立绘）默认是「优先魔珐」，
        但没有这一项时，只要填了密钥就永远走魔珐——本地渲染器根本没机会跑。
        用 fieldset + legend 让读屏能按组播报（且 radio 用同一 name 保证互斥）。
      */}
      <fieldset className="mt-4">
        <legend className="text-xs text-ink-muted">渲染方式</legend>
        <div className="mt-2 space-y-1.5">
          {AVATAR_RENDERER_OPTIONS.map((option) => {
            const selected = renderer === option.value;
            return (
              <label
                key={option.value}
                className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-colors ${
                  selected
                    ? "border-accent bg-accent-soft"
                    : "border-line hover:bg-surface-hover"
                }`}
              >
                <input
                  type="radio"
                  name={`${panelId}-renderer`}
                  value={option.value}
                  checked={selected}
                  onChange={() => onRendererChange(option.value)}
                  className="mt-0.5 accent-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
                />
                <span className="flex-1">
                  <span className="block text-xs font-medium text-ink">{option.label}</span>
                  <span className="block text-[11px] leading-relaxed text-ink-soft">
                    {option.hint}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {/* 模型库：上传自己的模型 + 选用 + （静态立绘）逐个指定情绪 */}
      <ModelLibraryPanel models={models} />

      {/*
        语音引擎：Live2D / 静态立绘的「真声音」来自服务端 TTS（GPT-SoVITS）。
        自动判断只在后端就绪那一刻取一次，现场可能想手动固定一路，所以给三档。
      */}
      <fieldset className="mt-4">
        <legend className="text-xs text-ink-muted">语音引擎</legend>
        <div className="mt-2 space-y-1.5">
          {TTS_ENGINE_OPTIONS.map((option) => {
            const selected = ttsEngine === option.value;
            return (
              <label
                key={option.value}
                className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-colors ${
                  selected ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-hover"
                }`}
              >
                <input
                  type="radio"
                  name={`${panelId}-tts-engine`}
                  value={option.value}
                  checked={selected}
                  onChange={() => onTtsEngineChange(option.value)}
                  className="mt-0.5 accent-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
                />
                <span className="flex-1">
                  <span className="block text-xs font-medium text-ink">{option.label}</span>
                  <span className="block text-[11px] leading-relaxed text-ink-soft">
                    {option.hint}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
        <p role="status" className="mt-2 text-[11px] leading-relaxed text-ink-soft">
          {ttsNote}
        </p>
      </fieldset>

      {/* 音色（服务端 TTS 专属）：音色 = 参考音频 + 参考文本，由后端音色表定义 */}
      {showVoices && (
        <div className="mt-3 rounded-lg border border-line px-3 py-2">
          <label className="block">
            <span className="text-xs text-ink-muted">音色（GPT-SoVITS）</span>
            <select
              value={ttsVoice}
              onChange={(event) => onTtsVoiceChange(event.target.value)}
              className="focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink focus:border-accent"
            >
              {/* 文案随后端实际行为变：配了「情绪→音色」映射时，“不选”不等于“固定一个音色” */}
              <option value="">
                {ttsStatus?.emotion_voices ? "自动（按情绪选音色）" : "默认音色（后端配置）"}
              </option>
              {voiceOptions.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.label}
                </option>
              ))}
            </select>
          </label>
          <div className="mt-2 flex items-center justify-between gap-2">
            <p className="text-[11px] leading-relaxed text-ink-soft">
              音色表在后端 <span className="text-ink-muted">backend/data/tts_voices.json</span>
              ，改动后需重启后端生效。
              {ttsStatus?.emotion_voices
                ? "不选音色时按本轮情绪自动选（映射也在该文件里）。"
                : ""}
            </p>
            <button
              type="button"
              onClick={handlePreview}
              disabled={!onPreviewVoice || previewing}
              className="focus-ring shrink-0 rounded-lg border border-line px-3 py-1.5 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:text-ink-faint"
            >
              {previewing ? "试听中…" : "试听"}
            </button>
          </div>
        </div>
      )}

      <div className="mt-4 space-y-3">
        <label className="block">
          <span className="text-xs text-ink-muted">App ID（AK）</span>
          <input
            ref={firstFieldRef}
            value={appId}
            onChange={(event) => setAppId(event.target.value)}
            placeholder="例如 c8fc6578…"
            autoComplete="off"
            className="focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent"
          />
        </label>

        <label className="block">
          <span className="text-xs text-ink-muted">App Secret</span>
          {/* 组合控件：焦点落在内部任一元素时整组高亮（审计 F2） */}
          <div className="mt-1 flex gap-2 rounded-lg focus-within:ring-2 focus-within:ring-accent/60 focus-within:ring-offset-2 focus-within:ring-offset-surface-panel">
            <input
              value={appSecret}
              onChange={(event) => setAppSecret(event.target.value)}
              type={revealSecret ? "text" : "password"}
              placeholder="••••••••"
              autoComplete="off"
              className="focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent"
            />
            <button
              type="button"
              onClick={() => setRevealSecret((prev) => !prev)}
              className="focus-ring shrink-0 rounded-lg border border-line px-3 text-xs text-ink-soft transition-colors hover:bg-surface-hover"
            >
              {revealSecret ? "隐藏" : "显示"}
            </button>
          </div>
        </label>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={!canSave}
          className="focus-ring flex-1 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
        >
          保存并启用
        </button>
        <button
          type="button"
          onClick={handleClear}
          className="focus-ring rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:bg-surface-hover"
        >
          清除
        </button>
      </div>

      {message && (
        <p role="status" className="mt-2 text-xs text-success-text">
          {message}
        </p>
      )}

      <div className="mt-3 space-y-1 border-t border-line pt-3 text-xs leading-relaxed text-ink-soft">
        <p>
          密钥获取：魔珐星云控制台 → <span className="text-ink-muted">应用中心</span> → 创建
          <span className="text-ink-muted">驱动应用</span> → 查看密钥 → 复制 App ID / App Secret。
        </p>
        <p>
          密钥仅保存在本机浏览器，不会上传到服务器；「本地渲染器」用项目自带的 Live2D
          模型（需已执行 scripts/setup-cubism.mjs），不消耗魔珐积分。
        </p>
      </div>
    </div>
  );
}
