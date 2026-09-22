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

import { AgentBadge } from "@/components/AgentBadge";
import { AvatarSettings } from "@/components/AvatarSettings";
import { AvatarStage } from "@/components/AvatarStage";
import { ChatPanel } from "@/components/ChatPanel";
import { KnowledgePanel } from "@/components/KnowledgePanel";
import { LlmSettings } from "@/components/LlmSettings";
import { MemoryTrace } from "@/components/MemoryTrace";
import { MoodIndicator } from "@/components/MoodIndicator";
import { PersonaSwitcher } from "@/components/PersonaSwitcher";
import { PresetSwitcher } from "@/components/PresetSwitcher";
import { SessionList } from "@/components/SessionList";
import { StPresetPanel } from "@/components/StPresetPanel";
import { StyleSwitcher } from "@/components/StyleSwitcher";
import { SubtitleBar } from "@/components/SubtitleBar";
import { useBrowserAvatar, useXmovAvatar } from "@/hooks/useAvatar";
import { useAvatarCredentials } from "@/hooks/useAvatarCredentials";
import { useChatSession } from "@/hooks/useChatSession";
import { getHealth, getLlmConfig, getPersonas, getPresets, getStyles } from "@/lib/api";
import type {
  McpServerStatus,
  PersonaCatalog,
  PresetCatalog,
  StyleCatalog,
} from "@/lib/types";

const CONTAINER_ID = "avatar-container"; // 用于 DOM 元素的 id
const CONTAINER_SELECTOR = "#avatar-container"; // 传给 SDK 的 CSS 选择器（兜底）
const SETTINGS_PANEL_ID = "avatar-settings-panel";
const LLM_PANEL_ID = "llm-settings-panel";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-base";

export default function HomePage() {
  const { credentials, source, configured, revision, save, clear } = useAvatarCredentials();
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** 对话模型设置面板开关 */
  const [llmOpen, setLlmOpen] = useState(false);
  /** 当前生效的模型名（顶栏展示；来源是 GET /llm/config） */
  const [llmModel, setLlmModel] = useState("");
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  /** MCP 外部服务连接状态（行动层可见性证据） */
  const [mcpServers, setMcpServers] = useState<McpServerStatus[]>([]);
  /** 模型预设档清单（选择器数据源；后端不可用时为 null，选择器不渲染） */
  const [presetCatalog, setPresetCatalog] = useState<PresetCatalog | null>(null);
  /** 文风清单（来自 GET /chat/styles，替代原先前端硬编码的 4 项） */
  const [styleCatalog, setStyleCatalog] = useState<StyleCatalog | null>(null);
  /** 人设（陪伴对象）清单 */
  const [personaCatalog, setPersonaCatalog] = useState<PersonaCatalog | null>(null);
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

  /**
   * 「用户是否手动选过」标记：手动选过之后，后端声明的缺省值不再覆盖用户选择。
   */
  const personaTouchedRef = useRef(false);
  const styleTouchedRef = useRef(false);
  /** 当前值的最新引用（供只跑一次的回调读取，避免把它写进 effect 依赖） */
  const personaIdRef = useRef(session.personaId);
  personaIdRef.current = session.personaId;
  const styleIdRef = useRef(session.styleId);
  styleIdRef.current = session.styleId;

  /**
   * 当前角色名（页面标题用）。
   * 后端离线时回退到项目默认角色名——这只是首屏兜底，不是硬编码来源：
   * 一旦 GET /chat/personas 返回，名字就跟着服务端预设文件走。
   */
  const personaName =
    personaCatalog?.personas.find((persona) => persona.id === session.personaId)?.name ?? "苏澄";

  /**
   * 取出引用稳定的两个 setter。
   *
   * `session` 对每次渲染都是新对象（hook 直接返回字面量），直接作为 effect 依赖
   * 会让 effect 每次渲染都重跑（→ 请求风暴）；而这两个函数分别来自 `useState`
   * 与 `useCallback([], ...)`，引用是稳定的，单独抽出即可表达真实依赖。
   */
  const { setStyleId: changeStyle, setPersonaId: changePersona } = session;

  // 后端健康检查：未就绪时每 5s 重试（后端稍后启动也能自动恢复），卸载时停止
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const check = async () => {
      const health = await getHealth();
      if (cancelled) return;
      const online = health?.status === "ok";
      setBackendOnline(online);
      setMcpServers(health?.mcp ?? []);
      if (!online) timer = setTimeout(check, 5000);
    };
    check();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // 界面选项清单：后端就绪后各拉一次（清单由服务端预设文件决定，不会频繁变）
  useEffect(() => {
    if (backendOnline !== true) return;
    getPresets().then((catalog) => setPresetCatalog(catalog));
    void getLlmConfig().then((data) => setLlmModel(data?.config.model ?? ""));
    void getStyles().then((catalog) => {
      setStyleCatalog(catalog);
      if (!catalog || styleTouchedRef.current) return;
      // 缺省文风以后端配置为准（前端不再硬编码）；
      // 配置与清单不一致时退回清单首项——否则会把后端不认识的 style_id 发出去
      const declared = catalog.default_style_id;
      const candidate = catalog.styles.some((style) => style.id === declared)
        ? declared
        : catalog.styles[0]?.id;
      if (candidate && candidate !== styleIdRef.current) {
        changeStyle(candidate);
      }
    });
  }, [backendOnline, changeStyle]);

  // 人设清单：同时把后端声明的缺省人设同步进来（替代前端硬编码常量）
  useEffect(() => {
    if (backendOnline !== true) return;
    void getPersonas().then((catalog) => {
      setPersonaCatalog(catalog);
      if (!catalog || personaTouchedRef.current) return;
      // 同文风：后端声明的缺省人设若不在清单里（配置写错），退回清单首项——
      // 否则会把一个后端不认识的人设 id 发出去，对话直接 404
      const declared = catalog.default_persona_id;
      const candidate = catalog.personas.some((persona) => persona.id === declared)
        ? declared
        : catalog.personas[0]?.id;
      if (candidate && candidate !== personaIdRef.current) {
        changePersona(candidate);
      }
    });
  }, [backendOnline, changePersona]);

  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-6xl flex-col gap-4 p-4 lg:h-[100dvh] lg:min-h-0 lg:overflow-hidden lg:p-6">
      <header className="relative flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-balance text-lg font-semibold text-ink">
            <span translate="no">HyPRA</span> ·{" "}
            <span translate="no" className="text-accent-text">
              {personaName}
            </span>
          </h1>
          <p className="text-xs text-ink-soft">
            情感陪伴 3D 交互系统 · 分层提示词 + 混合记忆 + 情绪链路 + Agent 行动层
          </p>
        </div>

        <div className="relative flex items-center gap-2">
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
          <AgentBadge servers={mcpServers} />
          <button
            type="button"
            aria-expanded={llmOpen}
            aria-controls={LLM_PANEL_ID}
            onClick={() => setLlmOpen((prev) => !prev)}
            className={`rounded-full bg-surface-raised px-3 py-1 text-xs text-ink-muted ring-1 ring-line transition-colors hover:bg-surface-hover ${FOCUS_RING}`}
          >
            模型{llmModel ? ` · ${llmModel}` : ""}
          </button>
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

        <LlmSettings
          open={llmOpen}
          onClose={() => setLlmOpen(false)}
          panelId={LLM_PANEL_ID}
          onApplied={(config) => {
            setLlmModel(config.model);
            // 预设档按新模型名重新匹配，清单要跟着刷新（否则「自动」档还是旧的）
            void getPresets().then((catalog) => setPresetCatalog(catalog));
          }}
        />
      </header>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1.05fr_1fr]">
        {/* 左：数字人 + 字幕（高度受限时内部滚动，不把页面撞长） */}
        <div className="flex min-h-0 flex-col gap-3 lg:overflow-y-auto">
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

        {/* 右：情绪 + 文风 + 个人记忆 + 对话（内容超高时列内滚动，不裁剪） */}
        <div className="flex min-h-0 flex-col gap-3 lg:overflow-y-auto">
          <MoodIndicator emotion={session.emotion} tone={session.tone} />
          <MemoryTrace
            memoryCounts={session.memoryCounts}
            knowledgeHits={session.knowledgeHits}
            worldbookHits={session.worldbookHits}
            memoryScheduled={session.memoryScheduled}
            estimatedTokens={session.estimatedTokens}
            warnings={session.warnings}
          />
          <PersonaSwitcher
            value={session.personaId}
            onChange={(id) => {
              personaTouchedRef.current = true;
              session.setPersonaId(id);
            }}
            catalog={personaCatalog}
            disabled={session.busy}
          />
          <SessionList
            personaId={session.personaId}
            activeSessionId={session.sessionId}
            version={session.sessionVersion}
            disabled={session.busy}
            onSelect={(id) => void session.openSession(id)}
            onNew={session.newSession}
            onRemove={session.removeSession}
          />
          <StyleSwitcher
            value={session.styleId}
            onChange={(id) => {
              styleTouchedRef.current = true;
              session.setStyleId(id);
            }}
            catalog={styleCatalog}
            disabled={session.busy}
          />
          <PresetSwitcher
            value={session.presetId}
            onChange={session.setPresetId}
            catalog={presetCatalog}
            disabled={session.busy}
          />
          <StPresetPanel
            value={session.stPresetId}
            onChange={session.setStPresetId}
            disabled={session.busy}
            lastRun={session.stPresetMeta}
          />
          <KnowledgePanel companionId={session.personaId} disabled={session.busy} />
          {/* min-h 兑底：知识库面板展开时对话区不被压到不可用高度 */}
          <div className="min-h-[320px] flex-1">
            <ChatPanel
              messages={session.messages}
              toolsUsed={session.toolsUsed}
              busy={session.busy}
              error={session.error}
              onSend={session.send}
              onInterrupt={session.interrupt}
              streamingSpeech={session.streamingSpeech}
              onToggleStreaming={session.setStreamingSpeech}
              personaId={session.personaId}
              restoring={session.restoring}
              historyError={session.historyError}
              onRetryHistory={() => void session.retryHistory()}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
