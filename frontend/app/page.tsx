"use client";

/**
 * HyPRA 主页面：左侧数字人舞台 + 右侧对话与情绪面板。
 *
 * 数字人渲染 provider（自动选择 + 自动降级）：
 *   - 已配置凭证（**页面「数字人设置」填写** 或 构建时环境变量）→ **魔珐具身驱动 SDK**（真实 3D）
 *   - 未配置 / SDK 加载失败 / init 失败 → **本地渲染器（Live2D / 立绘）+ 静默**（演示不中断）
 *
 * 凭证可在页面上直接填写（存 localStorage，即时生效，无需重新构建）。
 *
 * 降级状态设计：用「凭证版本」绑定降级标记，provider 在渲染期直接派生
 * ——首屏即为正确 provider（不会先渲染一帧降级态），凭证重新保存后标记自动失效。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { AgentBadge } from "@/components/settings/AgentBadge";
import { AvatarSettings } from "@/components/avatar/AvatarSettings";
import { AvatarStage } from "@/components/avatar/AvatarStage";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { KnowledgePanel } from "@/components/studio/KnowledgePanel";
import { LlmSettings } from "@/components/settings/LlmSettings";
import { MemoryTrace } from "@/components/chat/MemoryTrace";
import { MoodIndicator } from "@/components/chat/MoodIndicator";
import { PersonaSwitcher } from "@/components/settings/PersonaSwitcher";
import { JailbreakSwitcher } from "@/components/settings/JailbreakSwitcher";
import { UserNameField } from "@/components/settings/UserNameField";
import { ModeSwitcher } from "@/components/settings/ModeSwitcher";
import { PluginCenter } from "@/components/settings/PluginCenter";
import { PresetSwitcher } from "@/components/settings/PresetSwitcher";
import { SkillPanel } from "@/components/settings/SkillPanel";
import { SessionList } from "@/components/chat/SessionList";
import { StPresetPanel } from "@/components/studio/StPresetPanel";
import { StudioPanel } from "@/components/studio/StudioPanel";
import { StyleSwitcher } from "@/components/settings/StyleSwitcher";
import { SubtitleBar } from "@/components/chat/SubtitleBar";
import { useServerTtsAvatar, useSilentAvatar, useXmovAvatar } from "@/hooks/avatar/useAvatar";
import { useTtsPreferences } from "@/hooks/avatar/useTtsPreferences";
import { useAvatarCredentials } from "@/hooks/avatar/useAvatarCredentials";
import { useChatSession } from "@/hooks/chat/useChatSession";
import {
  getHealth,
  getJailbreakPresets,
  getLlmConfig,
  getPersonas,
  getPresets,
  getStyles,
} from "@/lib/api/client";
import type {
  JailbreakCatalog,
  McpServerStatus,
  PersonaCatalog,
  PluginStatus,
  PluginsSummary,
  SkillInfo,
  PresetCatalog,
  StyleCatalog,
} from "@/lib/api/types";
import { useAvatarModels } from "@/hooks/avatar/useAvatarModels";
import { useLipSyncTimeline } from "@/hooks/avatar/useLipSyncTimeline";
import { resolveAvatarModelSource } from "@/lib/avatar/avatar-model-source";
import { resolveVoiceSource } from "@/lib/avatar/avatar-config";
import {
  personaName as resolvePersonaName,
  personaTitle as resolvePersonaTitle,
} from "@/lib/chat/persona";

const CONTAINER_ID = "avatar-container"; // 用于 DOM 元素的 id
const CONTAINER_SELECTOR = "#avatar-container"; // 传给 SDK 的 CSS 选择器（兜底）

/**
 * 右侧面板的标签页。
 *
 * 为什么要分页：此前「人设 / 称呼 / 历史 / 文风 / 预设 / 酒馆预设 / 模式 /
 * 能力中心 / 技能 / 知识库 / 对话」十几块面板**竖直堆在一列**里，
 * 结果是对话——这个页面真正的主角——被挤到最底下，要滚很久才看得到。
 * 分页之后对话独占一屏，配置项各归其位，且不再随对话高度互相挤压。
 *
 * 面板用 `hidden` 隐藏而不是卸载：切回来时状态（展开的列表、已加载的清单）
 * 还在，也不会因为来回切换而反复打后端接口。
 */
const TABS = [
  { id: "chat", label: "对话" },
  { id: "companion", label: "陪伴" },
  { id: "capability", label: "能力" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * 试听文本：短、且能听出音色差异。
 *
 * 用固定句式而不是随机文本：试听是**对比音色**的，句式一变就不好对比了；
 * 但角色名跟着当前人设走——写死一个名字，换人设后试听里还会报旧名字。
 */
function voicePreviewText(characterName: string): string {
  return `你好，我是${characterName}。今天想聊点什么？`;
}
const SETTINGS_PANEL_ID = "avatar-settings-panel";
const LLM_PANEL_ID = "llm-settings-panel";
const STUDIO_PANEL_ID = "studio-panel";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-base";

/**
 * 顶栏状态芯片。
 *
 * `dot` 决定要不要那颗状态点：状态类（后端在线 / 渲染器）带点，纯信息类（模型名）不带。
 * `pulse` 只给「活着且在动」的状态用（在线），静止的不要加动效，否则整条顶栏都在呼吸。
 */
type ChipTone = "neutral" | "success" | "danger" | "warning" | "accent" | "brand";

const CHIP_TONE_CLASS: Record<ChipTone, string> = {
  neutral: "chip-neutral",
  success: "chip-success",
  danger: "chip-danger",
  warning: "chip-warning",
  accent: "chip-accent",
  brand: "chip-brand",
};

function StatusChip({
  tone,
  dot = false,
  pulse = false,
  title,
  children,
}: {
  tone: ChipTone;
  dot?: boolean;
  pulse?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span role="status" title={title} className={`chip ${CHIP_TONE_CLASS[tone]}`}>
      {dot ? <span aria-hidden className={`status-dot ${pulse ? "animate-pulse-dot" : ""}`} /> : null}
      {children}
    </span>
  );
}

/**
 * 顶栏图标按钮（模型 / 创作工坊 / 数字人设置）。
 *
 * 图标是装饰（`aria-hidden`），可访问名一律由可见文字提供——
 * 顶栏这些按钮都带文字标签，不需要再补 aria-label。
 */
function HeaderButton({
  open,
  controls,
  onClick,
  tone = "neutral",
  children,
}: {
  open: boolean;
  controls: string;
  onClick: () => void;
  tone?: "neutral" | "warning";
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onClick}
      className={`chip transition-colors ${FOCUS_RING} ${
        tone === "warning"
          ? "chip-warning hover:bg-warning/20"
          : open
            ? "chip-accent"
            : "chip-neutral hover:bg-surface-hover"
      }`}
    >
      {children}
    </button>
  );
}


export default function HomePage() {
  const { credentials, source, configured, revision, save, clear, renderer, setRenderer } =
    // Web 端用**横屏**应用（桌宠窗用竖屏，两套凭证分开存，见 useAvatarCredentials）
    useAvatarCredentials({ form: "web" });
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** 对话模型设置面板开关 */
  const [llmOpen, setLlmOpen] = useState(false);
  /** 创作工坊（自建角色 / 世界书）面板开关 */
  const [studioOpen, setStudioOpen] = useState(false);
  /** 右栏当前标签页（对话 / 陪伴 / 能力） */
  const [tab, setTab] = useState<TabId>("chat");
  /** 当前生效的模型名（顶栏展示；来源是 GET /llm/config） */
  const [llmModel, setLlmModel] = useState("");
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  /** MCP 外部服务连接状态（行动层可见性证据） */
  const [mcpServers, setMcpServers] = useState<McpServerStatus[]>([]);
  // 插件体系状态（AGENTS.md §9）：随 /health 轮询刷新，驱动「能力中心」面板
  const [plugins, setPlugins] = useState<PluginStatus[]>([]);
  const [pluginsSummary, setPluginsSummary] = useState<PluginsSummary | null>(null);
  /**
   * 手动重拉 /health 的触发器：插件启停 / 保存配置后，
   * summary 的计数（运行中 / 失败）只有后端知道——重新拉一次，
   * 好过在前端复刻一份 summary 计算逻辑、然后与后端慢慢漂移。
   * 技能面板的启停 / 重扫也复用同一个触发器。
   */
  const [healthNonce, setHealthNonce] = useState(0);
  // 技能清单（AGENTS.md §9.6）：同样随 /health 刷新，与插件体系相互独立
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  /** 模型预设档清单（选择器数据源；后端不可用时为 null，选择器不渲染） */
  const [presetCatalog, setPresetCatalog] = useState<PresetCatalog | null>(null);
  /** 文风清单（来自 GET /chat/styles，替代原先前端硬编码的 4 项） */
  const [styleCatalog, setStyleCatalog] = useState<StyleCatalog | null>(null);
  /**
   * 叙事框架（jailbreak）清单 + 部署默认开关。
   *
   * 后端读不到时为 null，`JailbreakSwitcher` 自己会不渲染——与文风 / 人设
   * 同一套降级方式，页面不需要额外的判空分支。
   */
  const [jailbreakCatalog, setJailbreakCatalog] = useState<JailbreakCatalog | null>(
    null,
  );
  /** 人设（陪伴对象）清单 */
  const [personaCatalog, setPersonaCatalog] = useState<PersonaCatalog | null>(null);
  /** SDK 降级标记（记录是哪一版凭证降级的，凭证变化后自动失效） */
  const [degraded, setDegraded] = useState<{ version: string; reason: string } | null>(null);
  /**
   * 语音引擎与音色偏好（含 `GET /media/tts/voices` 探测）。
   *
   * 后端就绪后才探测（与其它清单接口一致）；探测失败 → status=null → 静默。
   */
  const tts = useTtsPreferences({ enabled: backendOnline === true });

  // 凭证版本：内容或修订号变化即视为「新一版」（重新保存后允许重连）
  const credentialsVersion = `${credentials?.appId ?? ""}:${credentials?.appSecret ?? ""}:${revision}`;
  const degradedNow = degraded?.version === credentialsVersion ? degraded : null;

  /**
   * 渲染器选择（三层降级链的上层决策）。
   *
   * - `auto`：有密钥且未降级 → 魔珐；否则本地渲染器（Live2D → 静态立绘）
   * - `local` / `static`：**强制本地**——这正是「填了密钥也想看 Live2D」的场景，
   *   旧逻辑只看密钥有无，导致本地渲染器永远没机会跑
   * - `xmov`：强制魔珐（无密钥时下面会给出明确原因）
   */
  const xmovRequested = renderer === "xmov" || (renderer === "auto" && Boolean(credentials));
  const provider: "xmov" | "browser" =
    xmovRequested && Boolean(credentials) && !degradedNow ? "xmov" : "browser";
  const effectiveError =
    degradedNow?.reason ??
    avatarError ??
    (renderer === "xmov" && !credentials
      ? "已选择「魔珐星云 SDK」，但尚未填写 App ID / App Secret。"
      : null);

  const silentAvatar = useSilentAvatar();
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

  /** 服务端 TTS：音色取设置里选的那个（空串 = 后端默认音色） */
  const serverTtsAvatar = useServerTtsAvatar({ voice: tts.voice });

  /**
   * 播报实现的选择规则（纯函数，见 `lib/avatar-config.ts` 的 `resolveVoiceSource`）：
   * 魔珐路径只能用它自带 TTS；其余情况按「用户偏好 + 探测结果」决定——
   * **没接入 TTS 就是静默**（`none`），不再回落浏览器语音。
   */
  const voiceSource = resolveVoiceSource({
    xmovActive: provider === "xmov",
    serverTtsAvailable: tts.serverTtsAvailable,
    preference: tts.engine,
  });
  const avatar =
    voiceSource === "xmov"
      ? xmovAvatar
      : voiceSource === "server"
        ? serverTtsAvatar
        : silentAvatar;

  const session = useChatSession(avatar);

  /**
   * 当前角色名与一句话定位（页面标题、数字人舞台、对话面板、试听文本共用）。
   *
   * 清单未回来时用中性兜底词，**不回退某个具体角色名**：内置人设只是示例之一，
   * 切换人设后界面还顶着旧名字会让演示与自建角色对不上。
   */
  const currentPersonaName = resolvePersonaName(personaCatalog, session.personaId);
  const currentPersonaTitle = resolvePersonaTitle(personaCatalog, session.personaId);

  /**
   * 口型时间轴：交给本地渲染器驱动嘴型。
   *
   * 两条来源，优先用前者：
   * - **服务端 TTS**：controller 的 `timeline`——与正在播放的音频**同源**（同一次
   *   `/media/avatar` 请求），所以是真正的音画同步；
   * - 魔珐路径：没有本地音频可对齐，只能另发一次请求让后端按文本估算
   *   （`useLipSyncTimeline`），只求“嘴会动”。
   *
   * 因此在服务端 TTS 路径上**刻意让 useLipSyncTimeline 保持 inactive**，
   * 避免多打一次无用的请求，也避免两条时间轴互相覆盖；
   * 静默路径（`none`）连声音都没有，自然也不估算口型。
   */
  const estimatedLipSync = useLipSyncTimeline({
    active: avatar.provider === "xmov" && avatar.state === "speak",
    text: session.subtitle,
    emotion: session.emotion?.label ?? null,
    intensity: session.emotion?.intensity,
  });
  const lipSync = avatar.timeline ?? estimatedLipSync;

  /**
   * 试听指定音色（设置面板的「试听」按钮）。
   *
   * 直接复用服务端 TTS 控制器：播报的打断/降级/超时/音频释放只应该有一份实现，
   * 且它已被 13 个用例覆盖（面板里不再写第二套播放逻辑）。
   */
  const previewVoice = useCallback(
    async (voice: string) => {
      await serverTtsAvatar.speak(voicePreviewText(currentPersonaName), undefined, { voice });
    },
    [serverTtsAvatar, currentPersonaName],
  );

  /**
   * 模型库与当前选中的模型。
   *
   * `source` 把「选中的模型」翻译成渲染器能用的 URL：没选/后端拿不到时
   * 回落到内置模型，因此**不选任何模型也能正常演示**。
   */
  const avatarModels = useAvatarModels();
  const modelSource = resolveAvatarModelSource(avatarModels.selected);

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
   * 取出引用稳定的两个 setter。
   *
   * `session` 对每次渲染都是新对象（hook 直接返回字面量），直接作为 effect 依赖
   * 会让 effect 每次渲染都重跑（→ 请求风暴）；而这两个函数分别来自 `useState`
   * 与 `useCallback([], ...)`，引用是稳定的，单独抽出即可表达真实依赖。
   */
  const { setStyleId: changeStyle, setPersonaId: changePersona } = session;

  /**
   * 创作工坊改动后刷新「陪伴对象」与「文风」两份清单
   * （可能新增 / 删除 / 改名了角色，或新增 / 删除 / 改名了文风）。
   *
   * 文风必须跟着刷新：工坊里刚建好的文风若不出现在文风选择器里，用户会以为没建成。
   * 这里重新拉 `/chat/styles` 而不是复用 catalog 里的 `styles`——两者的形状不同
   * （工坊清单不含风格正文，选择器也不需要），各自拉各自的口径更不容易漂移。
   *
   * 必须先 useCallback 固定引用：StudioPanel 把它当依赖链的一环
   * （onCatalogChange → applyCatalog → load），每次渲染都给新函数会让面板
   * 反复重新加载 catalog。
   */
  const handleStudioCatalog = useCallback(() => {
    void getPersonas().then((next) => setPersonaCatalog(next));
    void getStyles().then((next) => setStyleCatalog(next));
  }, []);

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
      setPlugins(health?.plugins ?? []);
      setPluginsSummary(health?.plugins_summary ?? null);
      setSkills(health?.skills ?? []);
      if (!online) timer = setTimeout(check, 5000);
    };
    check();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [healthNonce]);

  // 界面选项清单：后端就绪后各拉一次（清单由服务端预设文件决定，不会频繁变）
  useEffect(() => {
    if (backendOnline !== true) return;
    getPresets().then((catalog) => setPresetCatalog(catalog));
    void getLlmConfig().then((data) => setLlmModel(data?.config.model ?? ""));
    /*
     * 叙事框架清单：**只取清单，不做「按部署默认校准」的写入**。
     *
     * 与下面文风 / 人设那两处不同——它们的校准会把缺省值写进偏好，因为
     * 「用哪份文风」本来就得有个具体答案。叙事框架是个**开关**，
     * 「未设置」本身是合法状态（= 跟随部署默认），交给组件在渲染时解析即可；
     * 挂载就写盘反而会把部署默认冻进用户偏好，日后改默认再也传不过来。
     */
    void getJailbreakPresets().then((catalog) => setJailbreakCatalog(catalog));
    void getStyles().then((catalog) => {
      setStyleCatalog(catalog);
      // 已有用户偏好（可能是在程序控制台里改的）→ 不用部署默认覆盖它
      if (!catalog || styleTouchedRef.current || session.hasStoredPreference) return;
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
  }, [backendOnline, changeStyle, session.hasStoredPreference]);

  // 人设清单：同时把后端声明的缺省人设同步进来（替代前端硬编码常量）
  useEffect(() => {
    if (backendOnline !== true) return;
    void getPersonas().then((catalog) => {
      setPersonaCatalog(catalog);
      // 同理：控制台里选好的人设优先于部署声明的缺省
      if (!catalog || personaTouchedRef.current || session.hasStoredPreference) return;
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
  }, [backendOnline, changePersona, session.hasStoredPreference]);

  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-[1400px] flex-col gap-4 p-4 lg:h-[100dvh] lg:min-h-0 lg:overflow-hidden lg:gap-5 lg:p-6">
      <header className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        {/* 品牌区：标记 + 标题 + 一句话定位 */}
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden="true"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-accent to-brand text-ink-on shadow-highlight"
          >
            {/* 抽象「声波 / 心跳」标记：两笔即可，不需要更复杂的图形 */}
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M4 13h3l2-5 3 9 2.5-6 2 3H20" />
            </svg>
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold leading-tight text-ink">
              <span translate="no">HyPRA</span>
              <span className="mx-1.5 text-ink-faint" aria-hidden="true">
                /
              </span>
              <span translate="no" className="text-accent-text">
                {currentPersonaName}
              </span>
            </h1>
            <p className="truncate text-xs text-ink-soft">
              情感陪伴 3D 交互系统 · 分层提示词 + 混合记忆 + 情绪链路 + Agent 行动层
            </p>
          </div>
        </div>

        {/* 状态与入口：状态芯片只读，右侧三个按钮各自开关一块浮层 */}
        <div className="relative flex flex-wrap items-center gap-2">
          <StatusChip
            tone={provider === "xmov" ? "brand" : "neutral"}
            dot
            title={provider === "xmov" ? "由魔珐星云 SDK 实时渲染" : "由本地渲染器渲染（Live2D / 静态立绘）"}
          >
            {provider === "xmov" ? "魔珐 SDK" : renderer === "static" ? "静态立绘" : "本地渲染器"}
          </StatusChip>
          <StatusChip
            tone={backendOnline === null ? "neutral" : backendOnline ? "success" : "danger"}
            dot
            pulse={backendOnline === true}
            title="后端 FastAPI 的连通状态"
          >
            {backendOnline === null ? "检测后端…" : backendOnline ? "后端在线" : "后端未连接"}
          </StatusChip>
          <AgentBadge servers={mcpServers} />

          <span aria-hidden="true" className="mx-0.5 hidden h-5 w-px bg-line sm:block" />

          <HeaderButton open={llmOpen} controls={LLM_PANEL_ID} onClick={() => setLlmOpen((prev) => !prev)}>
            模型{llmModel ? ` · ${llmModel}` : ""}
          </HeaderButton>
          <HeaderButton
            open={studioOpen}
            controls={STUDIO_PANEL_ID}
            onClick={() => setStudioOpen((prev) => !prev)}
          >
            创作工坊
          </HeaderButton>
          <HeaderButton
            open={settingsOpen}
            controls={SETTINGS_PANEL_ID}
            onClick={() => setSettingsOpen((prev) => !prev)}
            tone={configured ? "neutral" : "warning"}
          >
            数字人设置
          </HeaderButton>
        </div>

        <AvatarSettings
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          credentials={credentials}
          source={source}
          save={save}
          clear={clear}
          renderer={renderer}
          onRendererChange={setRenderer}
          ttsEngine={tts.engine}
          onTtsEngineChange={tts.setEngine}
          ttsVoice={tts.voice}
          onTtsVoiceChange={tts.setVoice}
          ttsStatus={tts.status}
          onPreviewVoice={previewVoice}
          models={avatarModels}
          panelId={SETTINGS_PANEL_ID}
        />

        <StudioPanel
          open={studioOpen}
          onClose={() => setStudioOpen(false)}
          disabled={session.busy}
          defaultScope={session.personaId}
          panelId={STUDIO_PANEL_ID}
          onCatalogChange={handleStudioCatalog}
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

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1.02fr)_minmax(0,1fr)] lg:gap-5">
        {/* 左：数字人 + 字幕（高度受限时内部滚动，不把页面撞长） */}
        <div className="flex min-h-0 flex-col gap-3 lg:overflow-y-auto">
          <AvatarStage
            state={avatar.state}
            emotion={session.emotion}
            provider={avatar.provider}
            renderer={renderer}
            source={modelSource}
            lipSync={lipSync}
            containerId={CONTAINER_ID}
            containerRef={containerRef}
            stage={avatar.stage}
            detail={avatar.detail}
            characterName={currentPersonaName}
            characterTitle={currentPersonaTitle}
            className="flex-1"
          />
          <SubtitleBar text={session.subtitle} active={avatar.state === "speak"} />

          {/* 数字人失败原因（不因降级而丢失，便于现场排查） */}
          {effectiveError && (
            <div role="status" className="notice notice-danger">
              <p className="break-words font-medium">数字人未启用：{effectiveError}</p>
              <p className="mt-1 leading-relaxed text-danger-text/80">
                已自动降级为本地渲染 + 静默播报（对话 / 字幕 / 情绪均不受影响，
                需要语音请在后端配好服务端 TTS）。常见原因：
                密钥不是「驱动应用」的、应用未完成配置、或网络无法访问魔珐服务。
                修正后点击右上角设置重新保存即可重连。
              </p>
            </div>
          )}

          {/* 本地渲染路径的提示（xmov 模式下由 SDK 自己的状态行负责） */}
          {provider === "browser" && (
            <p className="text-center text-[11px] leading-relaxed text-ink-soft">
              正在使用本地渲染器（Live2D / 静态立绘），对话与情绪联动不受影响。
              右上角「数字人设置」可切换渲染方式或填入魔珐密钥。
            </p>
          )}
        </div>

        {/* 右：情绪 / 记忆常驻，其余按标签页切换 */}
        <div className="flex min-h-0 flex-col gap-3">
          {/* 这两块是核心能力的实时证据（情绪链路 + 记忆召回），切到哪一页都要看得见 */}
          <MoodIndicator emotion={session.emotion} tone={session.tone} />
          <MemoryTrace
            mode={session.effectiveMode}
            memoryCounts={session.memoryCounts}
            knowledgeHits={session.knowledgeHits}
            worldbookHits={session.worldbookHits}
            memoryScheduled={session.memoryScheduled}
            estimatedTokens={session.estimatedTokens}
            warnings={session.warnings}
          />

          {/* 标签栏：分段控件。当前项用白底 + 投影「浮起来」，一眼能看出选中 */}
          <div
            role="tablist"
            aria-label="右侧面板"
            className="flex shrink-0 gap-1 rounded-2xl border border-line bg-surface-raised p-1"
          >
            {TABS.map((item) => {
              const active = item.id === tab;
              return (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  id={`tab-${item.id}`}
                  aria-selected={active}
                  aria-controls={`tabpanel-${item.id}`}
                  onClick={() => setTab(item.id)}
                  className={`flex-1 rounded-xl px-3 py-1.5 text-xs font-medium transition-all ${FOCUS_RING} ${
                    active
                      ? "bg-surface-panel text-ink shadow-card"
                      : "text-ink-muted hover:bg-surface-hover hover:text-ink"
                  }`}
                >
                  {item.label}
                </button>
              );
            })}
          </div>

          {/*
            面板容器：`hidden` 而非卸载。
            卸载会让 SessionList / KnowledgePanel 等每次切回来都重新拉接口、
            丢掉展开状态；`hidden` 保留状态且不产生额外请求。
            min-h-0 是让内部的滚动区（对话消息、配置列表）能正确收缩的前提。

            ⚠️ 容器上**不能同时写 `flex` 之类的 display 工具类**：UA 的
            `[hidden]{display:none}` 优先级低于任何一个 class，两者共存时
            `flex` 会赢、面板就藏不住了（实测过：两个标签页会一起显示）。
            所以显示方式一律交给内层元素，容器只负责「占位 + 可隐藏」。
          */}
          <div
            id="tabpanel-chat"
            role="tabpanel"
            aria-labelledby="tab-chat"
            hidden={tab !== "chat"}
            className="min-h-0 flex-1"
          >
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
              characterName={currentPersonaName}
              restoring={session.restoring}
              historyError={session.historyError}
              onRetryHistory={() => void session.retryHistory()}
              eventChannel={session.eventChannel}
              proactiveCount={session.proactiveCount}
              perceptionHint={session.perceptionHint}
            />
          </div>

          <div
            id="tabpanel-companion"
            role="tabpanel"
            aria-labelledby="tab-companion"
            hidden={tab !== "companion"}
            className="min-h-0 flex-1 overflow-y-auto pr-1"
          >
            <div className="flex flex-col gap-3 pb-1">
              <PersonaSwitcher
                value={session.personaId}
                onChange={(id) => {
                  personaTouchedRef.current = true;
                  session.setPersonaId(id);
                }}
                catalog={personaCatalog}
                disabled={session.busy}
              />
              <UserNameField
                idPrefix="web-user-name"
                value={session.userName}
                onSave={session.setUserName}
                disabled={session.busy}
              />
              <ModeSwitcher
                value={session.mode}
                effective={session.effectiveMode}
                onChange={session.setMode}
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
                onClearAll={session.clearAllSessions}
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
              <JailbreakSwitcher
                value={session.jailbreakId}
                onChange={session.setJailbreakId}
                catalog={jailbreakCatalog}
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
            </div>
          </div>

          <div
            id="tabpanel-capability"
            role="tabpanel"
            aria-labelledby="tab-capability"
            hidden={tab !== "capability"}
            className="min-h-0 flex-1 overflow-y-auto pr-1"
          >
            <div className="flex flex-col gap-3 pb-1">
              <PluginCenter
                plugins={plugins}
                summary={pluginsSummary}
                companionId={session.personaId}
                onCompanionChange={(id) => {
                  // 面板里的选择器与页面上的陪伴对象切换器是同一件事（记忆按对象隔离），
                  // 不联动就会出现「上面写着苏澄、导入面板写着陈小满」
                  personaTouchedRef.current = true;
                  session.setPersonaId(id);
                }}
                onPluginsChanged={(next) => {
                  // 先用响应里的快照就地更新（不闪烁），再后台重算 summary
                  setPlugins(next);
                  setHealthNonce((nonce) => nonce + 1);
                }}
              />
              <SkillPanel
                skills={skills}
                onSkillsChanged={() => setHealthNonce((nonce) => nonce + 1)}
              />
              <KnowledgePanel
                companionId={session.personaId}
                companionName={currentPersonaName}
                disabled={session.busy}
              />
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
