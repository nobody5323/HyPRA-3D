/**
 * 程序控制台（桌面端主入口）。
 *
 * 它承担两件事：
 * 1. 把**程序级**的东西集中到一处（后端、模型、插件、技能、预设、记忆、创作工坊…）；
 * 2. 决定以哪种模式启动——Web 端（浏览器里的完整体验）还是桌面模式（透明桌宠窗）。
 *
 * 界面复用 Web 端已有的面板组件（它们不依赖 `next/*`，可直接在 Vite 里渲染），
 * 因此本文件的职责是**分组、布局与桌面专属动作**，而不是把表单重写一遍。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ModelLibraryPanel } from "@/components/avatar/ModelLibraryPanel";
import { ModelSourcePanel } from "@/components/avatar/ModelSourcePanel";
import { LlmSettings } from "@/components/settings/LlmSettings";
import { PluginCenter } from "@/components/settings/PluginCenter";
import { SkillPanel } from "@/components/settings/SkillPanel";
import { StudioPanel } from "@/components/studio/StudioPanel";
import { useAvatarModels } from "@/hooks/avatar/useAvatarModels";
import { getHealth } from "@/lib/api/client";
import type { HealthStatus } from "@/lib/api/types";

import { MemoryPanel } from "./panels/MemoryPanel";
import { ModelDebugPanel } from "./panels/ModelDebugPanel";
import { PersonaPanel } from "./panels/PersonaPanel";
import { PetSettingsPanel } from "./panels/PetSettingsPanel";
import { ServicePanel } from "./panels/ServicePanel";
import { useConsoleBridge } from "./useConsoleBridge";
import { usePersonaScope } from "./usePersonaScope";

/** 左侧导航的分组 */
interface SectionMeta {
  id: string;
  label: string;
  hint: string;
  /** 图标名（见下面的 Icon） */
  icon: IconName;
  /** 接入进度说明（写在界面上，避免「点了没反应」） */
  pending?: string;
}

/**
 * 导航分组 = 「目前的功能都显示控制出来」的清单。
 *
 * `pending` 不是占位符而是**进度说明**：下一步会把对应的 Web 端面板接进来，
 * 名字直接写清楚，免得看的人以为这块被漏掉了。
 */
const SECTIONS: SectionMeta[] = [
  { id: "overview", label: "总览", hint: "后端与运行环境", icon: "gauge" },
  { id: "launch", label: "模式启动", hint: "以 Web / 桌面模式启动", icon: "play" },
  { id: "avatar", label: "形象调试", hint: "构图校准与实时预览", icon: "frame" },
  { id: "models", label: "模型库", hint: "上传 / 情绪映射 / 可获取的模型", icon: "layers" },
  { id: "llm", label: "模型连接", hint: "LLM 服务与推理参数", icon: "cpu" },
  { id: "plugins", label: "能力中心", hint: "插件分层 / 启停 / 配置 / 接入自己写的插件", icon: "puzzle" },
  { id: "skills", label: "技能", hint: "渐进式能力的清单与启停", icon: "sparkles" },
  { id: "persona", label: "人设与文风", hint: "人设 / 文风 / 提示词预设", icon: "idcard" },
  { id: "memory", label: "会话与记忆", hint: "会话列表 / 知识库 / 记忆轨迹", icon: "database" },
  { id: "studio", label: "创作工坊", hint: "角色卡 / 世界书 / 背景故事", icon: "palette" },
  { id: "pet", label: "桌宠设置", hint: "缩放 / 穿透 / 置顶", icon: "window" },
];

/**
 * 导航分组标题。
 *
 * 11 项平铺时看不出「哪些是运行、哪些是内容」，分组之后
 * 从「找到某一项」变成「知道它在哪一类里」——这才是侧栏存在的意义。
 */
const NAV_GROUPS: { title: string; ids: string[] }[] = [
  { title: "运行", ids: ["overview", "launch"] },
  { title: "形象", ids: ["avatar", "models"] },
  { title: "智能", ids: ["llm", "plugins", "skills"] },
  { title: "内容", ids: ["persona", "memory", "studio"] },
  { title: "桌面", ids: ["pet"] },
];

/**
 * 图标集（内联 SVG，不引第三方图标库）。
 *
 * 全部是 24×24 的线性图标：描边用 `currentColor`，所以颜色随父级文字色走，
 * 选中态（深色底白字）与常态（浅底灰字）共用同一份路径，不需要两套。
 */
type IconName =
  | "gauge"
  | "play"
  | "frame"
  | "layers"
  | "cpu"
  | "puzzle"
  | "sparkles"
  | "idcard"
  | "database"
  | "palette"
  | "window";

const ICON_PATHS: Record<IconName, string> = {
  gauge: "M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm0-10a9 9 0 0 1 7.5 13M12 4a9 9 0 0 0-7.5 13M13.5 11.5 17 8",
  play: "M7 4.5v15l12-7.5-12-7.5Z",
  frame: "M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z",
  layers: "m12 3 8.5 4.5L12 12 3.5 7.5 12 3Zm8.5 9L12 16.5 3.5 12M20.5 16.5 12 21l-8.5-4.5",
  cpu: "M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2M6 6h12v12H6zM10 10h4v4h-4z",
  puzzle: "M10 3.5a2 2 0 1 1 4 0V5h3.5a1.5 1.5 0 0 1 1.5 1.5V10h1.5a2 2 0 1 1 0 4H19v3.5a1.5 1.5 0 0 1-1.5 1.5H14v-1.5a2 2 0 1 0-4 0V19H6.5A1.5 1.5 0 0 1 5 17.5V14H3.5a2 2 0 1 1 0-4H5V6.5A1.5 1.5 0 0 1 6.5 5H10V3.5Z",
  sparkles: "M12 3.5 13.6 9 19 10.6 13.6 12.2 12 17.7 10.4 12.2 5 10.6 10.4 9 12 3.5ZM18.5 15.5l.7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z",
  idcard: "M3 5.5h18v13H3zM8.5 12.5a1.75 1.75 0 1 0 0-3.5 1.75 1.75 0 0 0 0 3.5ZM5.5 16c.6-1.3 1.7-2 3-2s2.4.7 3 2M14 10h4M14 13.5h4",
  database: "M4 6.5c0-1.4 3.6-2.5 8-2.5s8 1.1 8 2.5S16.4 9 12 9 4 7.9 4 6.5ZM4 6.5v11C4 18.9 7.6 20 12 20s8-1.1 8-2.5v-11M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5",
  palette: "M12 3a9 9 0 1 0 0 18 2 2 0 0 0 1.6-3.2 2 2 0 0 1 1.6-3.2H18a3 3 0 0 0 3-3A9 9 0 0 0 12 3ZM7.5 11a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm3-3.5a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm4.5 1a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
  window: "M3.5 5.5h17v13h-17zM3.5 9.5h17M6.5 7.5h.01M9 7.5h.01",
};

function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

/** 已接入内容的分组（其余分组落到「待接入」兑底卡片） */
const IMPLEMENTED = new Set([
  "overview",
  "launch",
  "avatar",
  "models",
  "llm",
  "plugins",
  "skills",
  "persona",
  "memory",
  "studio",
  "pet",
]);

/** 后端探测结果 */
interface BackendProbe {
  /** null = 还没测出结果 */
  online: boolean | null;
  status: HealthStatus | null;
  /** 最近一次探测耗时（ms），后端「活着但很慢」时能看出来 */
  latencyMs: number | null;
}

/**
 * 后端健康轮询。
 *
 * 控制台是长开窗口，所以按固定间隔轻量探测；`getHealth()` 在后端不可达时
 * 返回 null（不抛错），这里据此区分「离线」与「没测出来」。
 */
function useBackendProbe(): BackendProbe & { refresh: () => Promise<void> } {
  const [probe, setProbe] = useState<BackendProbe>({ online: null, status: null, latencyMs: null });
  const activeRef = useRef(true);

  const run = useCallback(async (): Promise<void> => {
    const started = performance.now();
    const status = await getHealth();
    const latencyMs = Math.round(performance.now() - started);

    if (!activeRef.current) {
      return;
    }

    setProbe({ online: status !== null, status, latencyMs });
  }, []);

  useEffect(() => {
    activeRef.current = true;
    void run();

    const timer = window.setInterval(() => void run(), 10_000);

    return () => {
      activeRef.current = false;
      window.clearInterval(timer);
    };
  }, [run]);

  return { ...probe, refresh: run };
}

/** 一行「标签 + 值」，总览与环境信息都用它 */
function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/70 py-2 last:border-0">
      <dt className="shrink-0 text-xs text-ink-soft">{label}</dt>
      <dd className="text-right text-xs text-ink">{value}</dd>
    </div>
  );
}

/**
 * 卡片外壳。
 *
 * `title` 用 h3（h1 是侧栏的程序名、h2 是内容区顶栏的当前分组），
 * 层级连续、读屏按标题跳转时不会跳错级。
 */
function Card({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  /** 标题右侧的操作（例如「刷新」），不传则只有标题 */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h3 className="card-title">{title}</h3>
          {description ? <p className="card-hint mt-1">{description}</p> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

/** 内容区顶栏的状态芯片（与控制台其它地方共用一套外观） */
function Chip({
  tone,
  dot = false,
  pulse = false,
  children,
}: {
  tone: "neutral" | "success" | "danger" | "warning" | "accent";
  dot?: boolean;
  pulse?: boolean;
  children: ReactNode;
}) {
  const TONE: Record<typeof tone, string> = {
    neutral: "chip-neutral",
    success: "chip-success",
    danger: "chip-danger",
    warning: "chip-warning",
    accent: "chip-accent",
  };

  return (
    <span className={`chip ${TONE[tone]}`}>
      {dot ? <span aria-hidden className={`status-dot ${pulse ? "animate-pulse-dot" : ""}`} /> : null}
      {children}
    </span>
  );
}

export function ConsoleApp() {
  const { bridge, state, settings } = useConsoleBridge();
  const probe = useBackendProbe();
  /** 模型选择与模型清单：控制台里多处共用同一份（调试台与模型库） */
  const avatarModels = useAvatarModels();
  /**
   * 当前陪伴对象（记忆隔离命名空间）。
   *
   * 三处要用它：人设与文风、会话与记忆、能力中心里的「酒馆记忆导入」。
   * 由本组件统一持有，不再让各面板自己算——**曾经就是这里出的错**：
   * 酒馆导入面板拿的是 `avatarModels.selectedId`（数字人模型 id，没选时是空串），
   * 于是面板退化成一句「请先选择一个陪伴对象」加一片空白。
   */
  const persona = usePersonaScope();
  const [section, setSection] = useState<string>("overview");
  const [webUrlDraft, setWebUrlDraft] = useState(settings.webUrl);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  /** 稳定的空回调：弹层组件把 onClose 收进依赖，每次渲染给新函数会让它们反复重载 */
  const noop = useCallback((): void => undefined, []);

  // 主进程回报的设置变化（比如另一处改了地址）要同步进输入框
  useEffect(() => {
    setWebUrlDraft(settings.webUrl);
  }, [settings.webUrl]);

  const runAction = useCallback(async (action: () => Promise<void>) => {
    setBusy(true);
    setNotice(null);

    try {
      await action();
    } catch (error: unknown) {
      setNotice({ tone: "warn", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  }, []);

  const petVisible = state?.petVisible ?? false;
  const environment = state?.environment ?? null;
  const current = SECTIONS.find((item) => item.id === section) ?? SECTIONS[0];

  return (
    <div className="flex h-full font-sans text-ink">
      {/* 左：分组导航 */}
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface-panel">
        <header className="flex items-center gap-2.5 px-4 py-4">
          <span
            aria-hidden="true"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-accent to-brand text-ink-on shadow-highlight"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M4 13h3l2-5 3 9 2.5-6 2 3H20" />
            </svg>
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold leading-tight">
              <span translate="no">HyPRA</span> 控制台
            </h1>
            <p className="text-[11px] text-ink-soft">配置 · 调试 · 启动</p>
          </div>
        </header>

        <nav aria-label="功能分组" className="flex-1 overflow-y-auto px-2 pb-3">
          {NAV_GROUPS.map((group) => {
            const items = SECTIONS.filter((item) => group.ids.includes(item.id));
            if (items.length === 0) return null;

            return (
              <div key={group.title} className="mb-3 last:mb-0">
                <p className="px-3 pb-1 text-[10px] font-medium uppercase tracking-wider text-ink-faint">
                  {group.title}
                </p>
                {items.map((item) => {
                  const active = item.id === section;

                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSection(item.id)}
                      aria-current={active ? "page" : undefined}
                      className={`group mb-0.5 flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors focus-ring ${
                        active
                          ? "bg-accent-soft text-accent-text"
                          : "text-ink-muted hover:bg-surface-hover hover:text-ink"
                      }`}
                    >
                      <span
                        className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg transition-colors ${
                          active
                            ? "bg-accent text-ink-on shadow-highlight"
                            : "bg-surface-raised text-ink-soft group-hover:text-ink"
                        }`}
                      >
                        <Icon name={item.icon} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium">{item.label}</span>
                        <span className="mt-0.5 block truncate text-[10px] leading-snug opacity-70">
                          {item.hint}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <footer className="border-t border-line px-4 py-3 text-[11px] text-ink-soft">
          <div className="flex items-center gap-2">
            <Chip
              tone={probe.online === null ? "neutral" : probe.online ? "success" : "danger"}
              dot
              pulse={probe.online === true}
            >
              {/* 三种状态分开写：探测中 / 在线 / 离线，别让「还不知道」看起来像「坏了」 */}
              {probe.online === null ? "检测中…" : probe.online ? "后端在线" : "后端未连接"}
            </Chip>
            {probe.latencyMs !== null && probe.online ? (
              <span className="tabular-nums text-ink-faint">{probe.latencyMs}ms</span>
            ) : null}
          </div>
          {environment ? (
            <p className="mt-2 truncate">
              v{environment.appVersion} · {environment.platform}
              {environment.development ? " · 开发模式" : ""}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => bridge?.quit()}
            className="btn btn-quiet btn-sm mt-2 w-full justify-start text-ink-muted"
          >
            <Icon name="window" className="h-3.5 w-3.5" />
            退出 <span translate="no">HyPRA</span>
          </button>
        </footer>
      </aside>

      {/* 右：内容区 */}
      <main className="consoleScroll flex-1 overflow-y-auto">
        {/*
          内容区顶栏。
          sticky + backdrop-blur：滚动长面板（模型库、记忆）时仍然知道自己在哪一页，
          也能随手点刷新——否则「滚到一半要回顶部才知道自己在看什么」。
        */}
        <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface-base/85 px-6 py-3 backdrop-blur-md">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-ink">{current.label}</h2>
            <p className="mt-0.5 text-[11px] leading-snug text-ink-soft">{current.hint}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Chip tone={petVisible ? "success" : "neutral"} dot>
              桌宠{petVisible ? "运行中" : "未启动"}
            </Chip>
            <button
              type="button"
              onClick={() => void probe.refresh()}
              className="btn btn-secondary btn-sm"
              title="重新探测后端状态与能力清单"
            >
              <Icon name="gauge" className="h-3.5 w-3.5" />
              刷新
            </button>
          </div>
        </header>

        <div className="px-6 py-5">
          {!bridge ? (
            <p className="notice notice-warning mb-4">
              浏览器调试模式：窗口控制不可用（
              <code className="rounded bg-surface-hover px-1">window.hyprConsole</code>{" "}
              未注入），下面的配置与启动动作不会真正生效。
            </p>
          ) : null}

          {notice ? (
            <p
              role="status"
              className={`notice mb-4 ${notice.tone === "ok" ? "notice-success" : "notice-danger"}`}
            >
              {notice.text}
            </p>
          ) : null}

          {section === "overview" ? (
            <div className="grid max-w-5xl gap-4 xl:grid-cols-2">
              <Card
                title="运行环境"
                description="桌面端进程与后端地址（后端地址来自构建期的 HYPRA_API_BASE）"
              >
                <dl>
                  <Fact label="后端地址" value={environment?.apiBase ?? "—"} />
                  <Fact label="桌面端版本" value={environment ? `v${environment.appVersion}` : "—"} />
                  <Fact label="运行平台" value={environment?.platform ?? "—"} />
                  <Fact
                    label="开发模式"
                    value={environment?.development ? "是（加载 Vite dev server）" : "否"}
                  />
                  <Fact
                    label="后端连通性"
                    value={
                      probe.online === null
                        ? "检测中…"
                        : probe.online
                          ? `在线${probe.latencyMs !== null ? ` · ${probe.latencyMs}ms` : ""}`
                          : "未连接"
                    }
                  />
                </dl>
              </Card>

              <Card
                title="当前模式"
                description="桌宠与 Web 端都不会自动打开——先在「模式启动」里选定，再回来这里看状态。"
              >
                <dl>
                  <Fact
                    label="桌面桌宠"
                    value={petVisible ? "运行中（桌面上可见）" : "未启动（已收起到托盘）"}
                  />
                  <Fact label="Web 端地址" value={settings.webUrl || "未配置"} />
                </dl>
              </Card>

              <div className="xl:col-span-2">
                <Card
                  title="本机服务"
                  description="桌面端代管 Qdrant、后端与 Web 前端三个本机进程，关掉 HyPRA 时一起收掉；其中 Web 前端按需启动（点「启动」，或在「以 Web 模式启动」时自动拉起）。"
                >
                  <ServicePanel
                    state={state}
                    bridge={bridge}
                    onNotice={(text) => setNotice({ tone: "ok", text })}
                  />
                </Card>
              </div>
            </div>
          ) : null}

        {section === "launch" ? (
          <div className="flex max-w-4xl flex-col gap-4">
            <Card
              title="桌面模式"
              description="无边框透明窗，常驻桌面；Live2D 模型与底部按钮的构图由「形象调试」决定。"
            >
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={busy || !bridge}
                  onClick={() =>
                    void runAction(async () => {
                      await bridge?.showPet();
                      setNotice({ tone: "ok", text: "桌宠已启动（可用托盘或 Ctrl+Alt+P 收起）" });
                    })
                  }
                  className="btn btn-primary btn-pill"
                >
                  <Icon name="play" className="h-3.5 w-3.5" />
                  {petVisible ? "重新聚焦桌宠" : "以桌面模式启动"}
                </button>
                <button
                  type="button"
                  disabled={busy || !bridge || !petVisible}
                  onClick={() =>
                    void runAction(async () => {
                      await bridge?.hidePet();
                      setNotice({ tone: "ok", text: "桌宠已收起（仍在托盘里，随时可唤出）" });
                    })
                  }
                  className="btn btn-secondary btn-pill"
                >
                  收起桌宠
                </button>
                <Chip tone={petVisible ? "success" : "neutral"} dot>
                  {petVisible ? "运行中" : "未启动"}
                </Chip>
              </div>
            </Card>

            <Card
              title="Web 模式"
              description="用系统浏览器打开 Web 端（Next.js 服务）。地址指向本机前端默认端口时会先把它拉起来、等就绪再开浏览器；指向远程地址（前端部署在别处）则直接打开。"
            >
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="url"
                  value={webUrlDraft}
                  onChange={(event) => setWebUrlDraft(event.target.value)}
                  placeholder="http://localhost:3000"
                  spellCheck={false}
                  className="field min-w-[240px] flex-1 py-1.5 text-xs"
                />
                <button
                  type="button"
                  disabled={busy || !bridge}
                  onClick={() =>
                    void runAction(async () => {
                      await bridge?.updateSettings({ webUrl: webUrlDraft });
                      setNotice({ tone: "ok", text: "Web 端地址已保存" });
                    })
                  }
                  className="btn btn-secondary btn-pill"
                >
                  保存地址
                </button>
                <button
                  type="button"
                  disabled={busy || !bridge}
                  onClick={() =>
                    void runAction(async () => {
                      // 前端没在跑时这一步会真的把它拉起来（首次要编译，可能等一会儿），
                      // 所以先给一句话，别让人以为点了没反应
                      setNotice({ tone: "ok", text: "正在确保 Web 前端已就绪…" });

                      const result = await bridge?.openWeb();

                      setNotice(
                        result?.ok
                          ? { tone: "ok", text: "Web 前端已就绪，已用系统浏览器打开" }
                          : { tone: "warn", text: `打开失败：${result?.reason ?? "未知原因"}` },
                      );
                    })
                  }
                  className="btn btn-primary btn-pill"
                >
                  以 Web 模式启动
                </button>
              </div>
              <p className="field-hint mt-2">
                只接受 http / https 地址（这是主进程打开外部程序的入口，不允许 file:// 之类的协议）。
              </p>
            </Card>
          </div>
        ) : null}

        {section === "avatar" ? (
          <div className="max-w-4xl">
            <Card
              title="模型调试台"
              description="调好的构图存进模型元数据（后端），Web 端与桌面端共用同一份——所以这里调一次，桌宠那边就对齐了。"
            >
              <ModelDebugPanel models={avatarModels} />
            </Card>
          </div>
        ) : null}

        {section === "models" ? (
          <div className="flex max-w-4xl flex-col gap-4">
            <Card
              title="模型库"
              description="上传的模型与「当前使用哪一个」都存后端，因此与 Web 端、桌宠端完全一致。"
            >
              <ModelLibraryPanel models={avatarModels} />
            </Card>
            <Card
              title="可获取的模型"
              description="本机模型库与清单里列出的模型：本项目只标注来源，不代下载、不分发模型。"
            >
              <ModelSourcePanel />
            </Card>
          </div>
        ) : null}

        {section === "plugins" ? (
          <div className="max-w-4xl">
            <Card title="能力中心" description="插件按必需性分层：core 不可禁用，builtin 可禁用/替换，third-party 由你自己写好后接入（见「添加插件」）。">
              <PluginCenter
                plugins={probe.status?.plugins ?? []}
                summary={probe.status?.plugins_summary ?? null}
                companionId={persona.personaId}
                onCompanionChange={(id) => {
                  void runAction(async () => {
                    await persona.select(id);
                  });
                }}
                onPluginsChanged={() => void probe.refresh()}
              />
            </Card>
          </div>
        ) : null}

        {section === "llm" ? (
          <div className="max-w-4xl">
            <Card
              title="模型连接"
              description="走 OpenAI 兼容 API（百炼 / 硅基流动 / 自建端点都行）；应用后下一轮对话即生效。"
            >
              <LlmSettings open onClose={noop} variant="inline" panelId="console-llm" />
            </Card>
          </div>
        ) : null}

        {section === "persona" ? (
          <div className="max-w-4xl">
            <Card
              title="人设与文风"
              description="这里改的是共享偏好（存后端）：Web 端与桌宠窗下次打开时按它生效。"
            >
              <PersonaPanel
                onOpenStudio={() => setSection("studio")}
                personaId={persona.personaId}
                personaCatalog={persona.catalog}
                onPersonaChange={(id) => {
                  void runAction(async () => {
                    await persona.select(id);
                    setNotice({
                      tone: "ok",
                      text: "陪伴对象已切换：Web 端与桌宠窗下次打开时按它生效",
                    });
                  });
                }}
              />
            </Card>
          </div>
        ) : null}

        {section === "memory" ? (
          <div className="max-w-4xl">
            <Card title="会话与记忆" description="四层记忆：工作记忆（会话内）/ 情景 / 语义 / 个人记忆。">
              <MemoryPanel
                personaId={persona.personaId}
                personaCatalog={persona.catalog}
                onPersonaChange={(id) => {
                  void runAction(async () => {
                    await persona.select(id);
                  });
                }}
              />
            </Card>
          </div>
        ) : null}

        {section === "studio" ? (
          <div className="max-w-4xl">
            <Card title="创作工坊" description="自建角色卡、背景故事与世界书条目（内置内容只读）。">
              {/*
                接上 onCatalogChange：新建 / 删除 / 改名角色后立刻重拉清单。
                不接的话「人设与文风」「会话与记忆」「酒馆记忆导入」三处的下拉
                都停留在旧清单——用户刚建好的角色在这一页选不到，
                看起来就像「生成完了没生效」（而对话链路其实已经认得它）。
              */}
              <StudioPanel
                open
                onClose={noop}
                variant="inline"
                panelId="console-studio"
                onCatalogChange={() => void persona.refresh()}
              />
            </Card>
          </div>
        ) : null}

        {section === "pet" ? (
          <div className="max-w-4xl">
            <Card title="桌宠设置" description="与桌宠窗内的设置面板共用同一份设置，改完两边一致。">
              <PetSettingsPanel
                state={state}
                bridge={bridge}
                onNotice={(text) => setNotice({ tone: "ok", text })}
              />
            </Card>
          </div>
        ) : null}

        {section === "skills" ? (
          <div className="max-w-4xl">
            <Card title="技能" description="清单常驻上下文，正文按需拉取（模型调 study_skill 时才注入）。">
              <SkillPanel
                skills={probe.status?.skills ?? []}
                onSkillsChanged={() => void probe.refresh()}
              />
            </Card>
          </div>
        ) : null}

        {!IMPLEMENTED.has(section) ? (
          <div className="max-w-4xl">
            {SECTIONS.filter((item) => item.id === section).map((item) => (
              <Card key={item.id} title={item.label} description={item.hint}>
                <p className="notice notice-info">{item.pending ?? "待接入"}</p>
              </Card>
            ))}
          </div>
        ) : null}
        </div>
      </main>
    </div>
  );
}
