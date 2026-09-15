"use client";

/**
 * HyPRA 主页面：左侧数字人舞台 + 右侧对话与情绪面板。
 *
 * 数字人渲染 provider（自动选择 + 自动降级）：
 *   - 已配置凭证（**页面「数字人设置」填写** 或 构建时环境变量）→ **魔珐具身驱动 SDK**（真实 3D）
 *   - 未配置 / SDK 加载失败 / init 失败 → **浏览器原生 TTS + 占位形象**（演示不中断）
 *
 * 凭证可在页面上直接填写（存 localStorage，即时生效，无需重新构建）。
 *
 * 降级状态设计：用「凭证版本」绑定降级标记，provider 在渲染期直接派生
 * ——首屏即为正确 provider（不会先渲染一帧降级态），凭证重新保存后标记自动失效。
 */

import { useEffect, useRef, useState } from "react";

import { AvatarSettings } from "@/components/AvatarSettings";
import { AvatarStage } from "@/components/AvatarStage";
import { ChatPanel } from "@/components/ChatPanel";
import { MoodIndicator } from "@/components/MoodIndicator";
import { StyleSwitcher } from "@/components/StyleSwitcher";
import { SubtitleBar } from "@/components/SubtitleBar";
import { useBrowserAvatar, useXmovAvatar } from "@/hooks/useAvatar";
import { useAvatarCredentials } from "@/hooks/useAvatarCredentials";
import { useChatSession } from "@/hooks/useChatSession";
import { getHealth } from "@/lib/api";

const CONTAINER_ID = "avatar-container"; // 用于 DOM 元素的 id
const CONTAINER_SELECTOR = "#avatar-container"; // 传给 SDK 的 CSS 选择器（兜底）
const SETTINGS_PANEL_ID = "avatar-settings-panel";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-base";

export default function HomePage() {
  const { credentials, source, configured, revision, save, clear } = useAvatarCredentials();
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  /** SDK 降级标记（记录是哪一版凭证降级的，凭证变化后自动失效） */
  const [degraded, setDegraded] = useState<{ version: string; reason: string } | null>(null);

  // 凭证版本：内容或修订号变化即视为「新一版」（重新保存后允许重连）
  const credentialsVersion = `${credentials?.appId ?? ""}:${credentials?.appSecret ?? ""}:${revision}`;
  const degradedNow = degraded?.version === credentialsVersion ? degraded : null;
  const provider: "xmov" | "browser" = credentials && !degradedNow ? "xmov" : "browser";
  const effectiveError = degradedNow?.reason ?? avatarError;

  const browserAvatar = useBrowserAvatar();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const xmovAvatar = useXmovAvatar(CONTAINER_SELECTOR, {
    credentials,
    enabled: provider === "xmov",
    revision,
    containerRef,
    onUnavailable: (reason) => {
      setDegraded({ version: credentialsVersion, reason }); // 保留失败原因（降级后仍可见）
    },
  });
  const avatar = provider === "xmov" ? xmovAvatar : browserAvatar;

  const session = useChatSession(avatar);

  // 后端健康检查：未就绪时每 5s 重试（后端稍后启动也能自动恢复），卸载时停止
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const check = async () => {
      const health = await getHealth();
      if (cancelled) return;
      const online = health?.status === "ok";
      setBackendOnline(online);
      if (!online) timer = setTimeout(check, 5000);
    };
    check();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col gap-4 p-4 lg:p-6">
      <header className="relative flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-balance text-lg font-semibold text-ink">
            <span translate="no">HyPRA</span> ·{" "}
            <span translate="no" className="text-accent-text">
              苏澄
            </span>
          </h1>
          <p className="text-xs text-ink-soft">
            情感陪伴 3D 交互系统 · 分层提示词 + 混合记忆 + 情绪链路 + Agent 行动层
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-3 py-1 text-xs ring-1 ${
              provider === "xmov"
                ? "bg-brand-soft text-brand-text ring-brand/30"
                : "bg-surface-raised text-ink-muted ring-line"
            }`}
          >
            {provider === "xmov" ? "魔珐 SDK" : "浏览器 TTS"}
          </span>
          <span
            role="status"
            className={`rounded-full px-3 py-1 text-xs ring-1 ${
              backendOnline === null
                ? "bg-surface-raised text-ink-soft ring-line"
                : backendOnline
                  ? "bg-success-soft text-success-text ring-success/30"
                  : "bg-danger-soft text-danger-text ring-danger/30"
            }`}
          >
            {backendOnline === null ? "检测后端…" : backendOnline ? "后端在线" : "后端未连接"}
          </span>
          <button
            type="button"
            aria-expanded={settingsOpen}
            aria-controls={SETTINGS_PANEL_ID}
            onClick={() => setSettingsOpen((prev) => !prev)}
            className={`rounded-full px-3 py-1 text-xs ring-1 transition-colors ${FOCUS_RING} ${
              configured
                ? "bg-surface-raised text-ink-muted ring-line hover:bg-surface-hover"
                : "bg-warning-soft text-warning-text ring-warning/30 hover:bg-warning/15"
            }`}
          >
            {configured ? "数字人设置" : "配置数字人密钥"}
          </button>
        </div>

        <AvatarSettings
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          credentials={credentials}
          source={source}
          save={save}
          clear={clear}
          panelId={SETTINGS_PANEL_ID}
        />
      </header>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1.05fr_1fr]">
        {/* 左：数字人 + 字幕 */}
        <div className="flex flex-col gap-3">
          <AvatarStage
            state={avatar.state}
            emotion={session.emotion}
            provider={avatar.provider}
            containerId={CONTAINER_ID}
            containerRef={containerRef}
            stage={avatar.provider === "xmov" ? xmovAvatar.stage : "ready"}
            detail={avatar.provider === "xmov" ? xmovAvatar.detail : ""}
          />
          <SubtitleBar text={session.subtitle} active={avatar.state === "speak"} />

          {/* 数字人失败原因（不因降级而丢失，便于现场排查） */}
          {effectiveError && (
            <div
              role="status"
              className="rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-xs text-danger-text"
            >
              <p className="break-words font-medium">数字人未启用：{effectiveError}</p>
              <p className="mt-1 leading-relaxed text-danger-text/80">
                已自动降级为浏览器语音（对话 / 字幕 / 情绪均不受影响）。常见原因：
                密钥不是「驱动应用」的、应用未完成配置、或网络无法访问魔珐服务。
                修正后点击右上角设置重新保存即可重连。
              </p>
            </div>
          )}

          {!configured && (
            <p className="text-center text-xs text-ink-soft">
              当前使用浏览器语音演示。点击右上角「配置数字人密钥」可启用真实 3D 数字人。
            </p>
          )}
        </div>

        {/* 右：情绪 + 文风 + 对话 */}
        <div className="flex min-h-0 flex-col gap-3">
          <MoodIndicator
            emotion={session.emotion}
            tone={session.tone}
            memoryCounts={session.memoryCounts}
          />
          <StyleSwitcher
            value={session.styleId}
            onChange={session.setStyleId}
            disabled={session.busy}
          />
          <div className="min-h-0 flex-1">
            <ChatPanel
              messages={session.messages}
              toolsUsed={session.toolsUsed}
              busy={session.busy}
              error={session.error}
              onSend={session.send}
              onInterrupt={session.interrupt}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
