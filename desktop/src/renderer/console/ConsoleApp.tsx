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
import { useConsoleBridge } from "./useConsoleBridge";

/** 左侧导航的分组 */
interface SectionMeta {
  id: string;
  label: string;
  hint: string;
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
  { id: "overview", label: "总览", hint: "后端与运行环境" },
  { id: "launch", label: "模式启动", hint: "以 Web / 桌面模式启动" },
  {
    id: "avatar",
    label: "形象调试",
    hint: "构图校准与实时预览",
  },
  {
    id: "models",
    label: "模型库",
    hint: "上传 / 情绪映射 / 可获取的模型",
  },
  {
    id: "llm",
    label: "模型连接",
    hint: "LLM 服务与推理参数",
  },
  {
    id: "plugins",
    label: "能力中心",
    hint: "插件分层 / 启停 / 配置",
  },
  { id: "skills", label: "技能", hint: "渐进式能力的清单与启停" },
  {
    id: "persona",
    label: "人设与文风",
    hint: "人设 / 文风 / 提示词预设",
  },
  {
    id: "memory",
    label: "会话与记忆",
    hint: "会话列表 / 知识库 / 记忆轨迹",
  },
  {
    id: "studio",
    label: "创作工坊",
    hint: "角色卡 / 世界书 / 背景故事",
  },
  {
    id: "pet",
    label: "桌宠设置",
    hint: "缩放 / 穿透 / 置顶",
  },
];

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
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-2 last:border-0">
      <dt className="shrink-0 text-xs text-ink-soft">{label}</dt>
      <dd className="text-right text-xs text-ink">{value}</dd>
    </div>
  );
}

/** 卡片外壳 */
function Card({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-line bg-surface-panel p-5">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {description ? <p className="mt-1 text-xs leading-relaxed text-ink-soft">{description}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function ConsoleApp() {
  const { bridge, state, settings } = useConsoleBridge();
  const probe = useBackendProbe();
  /** 模型选择与模型清单：控制台里多处共用同一份（调试台与模型库） */
  const avatarModels = useAvatarModels();
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

  return (
    <div className="flex h-full font-sans text-ink">
      {/* 左：分组导航 */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface-panel">
        <header className="px-4 py-4">
          <h1 className="text-sm font-semibold">
            <span translate="no">HyPRA</span> 程序控制台
          </h1>
          <p className="mt-0.5 text-[11px] text-ink-soft">配置 · 调试 · 启动</p>
        </header>

        <nav className="flex-1 overflow-y-auto px-2 pb-2">
          {SECTIONS.map((item) => {
            const active = item.id === section;

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSection(item.id)}
                aria-current={active ? "page" : undefined}
                className={`mb-0.5 w-full rounded-xl px-3 py-2 text-left transition-colors ${
                  active ? "bg-accent-soft text-accent-text" : "text-ink-muted hover:bg-surface-hover"
                }`}
              >
                <span className="block text-xs font-medium">{item.label}</span>
                <span className="mt-0.5 block text-[10px] leading-snug opacity-70">{item.hint}</span>
              </button>
            );
          })}
        </nav>

        <footer className="border-t border-line px-4 py-3 text-[11px] text-ink-soft">
          <p>
            后端{" "}
            {/* 三种状态分开写：探测中 / 在线 / 离线，别让「还不知道」看起来像「坏了」 */}
            {probe.online === null ? "检测中…" : probe.online ? "在线" : "未连接"}
            {probe.latencyMs !== null && probe.online ? ` · ${probe.latencyMs}ms` : ""}
          </p>
          {environment ? (
            <p className="mt-0.5">
              v{environment.appVersion} · {environment.platform}
              {environment.development ? " · 开发模式" : ""}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => bridge?.quit()}
            className="mt-2 w-full rounded-lg px-2 py-1 text-left text-[11px] text-ink-muted hover:bg-surface-hover"
          >
            退出 <span translate="no">HyPRA</span>
          </button>
        </footer>
      </aside>

      {/* 右：内容区 */}
      <main className="consoleScroll flex-1 overflow-y-auto p-6">
        {!bridge ? (
          <p className="mb-4 rounded-xl border border-warning/30 bg-warning-soft px-4 py-3 text-xs text-warning-text">
            浏览器调试模式：窗口控制不可用（
            <code className="rounded bg-surface-hover px-1">window.hyprConsole</code>{' '}
            未注入），下面的配置与启动动作不会真正生效。
          </p>
        ) : null}

        {notice ? (
          <p
            role="status"
            className={`mb-4 rounded-xl px-4 py-3 text-xs ${
              notice.tone === "ok"
                ? "border border-success/30 bg-success-soft text-success-text"
                : "border border-danger/30 bg-danger-soft text-danger-text"
            }`}
          >
            {notice.text}
          </p>
        ) : null}

        {section === "overview" ? (
          <div className="flex max-w-3xl flex-col gap-4">
            <Card title="运行环境" description="桌面端进程与后端地址（后端地址来自构建期的 HYPRA_API_BASE）">
              <dl>
                <Fact label="后端地址" value={environment?.apiBase ?? "—"} />
                <Fact label="桌面端版本" value={environment ? `v${environment.appVersion}` : "—"} />
                <Fact label="运行平台" value={environment?.platform ?? "—"} />
                <Fact label="开发模式" value={environment?.development ? "是（加载 Vite dev server）" : "否"} />
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
                <Fact label="桌面桌宠" value={petVisible ? "运行中（桌面上可见）" : "未启动（已收起到托盘）"} />
                <Fact
                  label="Web 端地址"
                  value={settings.webUrl || "未配置"}
                />
              </dl>
            </Card>
          </div>
        ) : null}

        {section === "launch" ? (
          <div className="flex max-w-3xl flex-col gap-4">
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
                  className="rounded-full bg-accent-soft px-4 py-1.5 text-xs text-accent-text ring-1 ring-accent/30 disabled:opacity-50"
                >
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
                  className="rounded-full bg-surface-raised px-4 py-1.5 text-xs text-ink-muted ring-1 ring-line disabled:opacity-50"
                >
                  收起桌宠
                </button>
                <span className="text-[11px] text-ink-soft">
                  {petVisible ? "运行中" : "未启动"}
                </span>
              </div>
            </Card>

            <Card
              title="Web 模式"
              description="用系统浏览器打开 Web 端（Next.js 服务）。需要它已经在跑；桌面端不代你启动服务，避免端口冲突与孤儿进程。"
            >
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="url"
                  value={webUrlDraft}
                  onChange={(event) => setWebUrlDraft(event.target.value)}
                  placeholder="http://localhost:3000"
                  spellCheck={false}
                  className="min-w-[240px] flex-1 rounded-xl border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
                  className="rounded-full bg-surface-raised px-4 py-1.5 text-xs text-ink-muted ring-1 ring-line disabled:opacity-50"
                >
                  保存地址
                </button>
                <button
                  type="button"
                  disabled={busy || !bridge}
                  onClick={() =>
                    void runAction(async () => {
                      const result = await bridge?.openWeb();

                      setNotice(
                        result?.ok
                          ? { tone: "ok", text: "已用系统浏览器打开 Web 端" }
                          : { tone: "warn", text: `打开失败：${result?.reason ?? "未知原因"}` },
                      );
                    })
                  }
                  className="rounded-full bg-accent-soft px-4 py-1.5 text-xs text-accent-text ring-1 ring-accent/30 disabled:opacity-50"
                >
                  以 Web 模式启动
                </button>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-ink-soft">
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
          <div className="flex max-w-3xl flex-col gap-4">
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
          <div className="max-w-3xl">
            <Card title="能力中心" description="插件按必需性分层：core 不可禁用，builtin 可禁用/替换，third-party 由用户安装。">
              <PluginCenter
                plugins={probe.status?.plugins ?? []}
                summary={probe.status?.plugins_summary ?? null}
                companionId={avatarModels.selectedId || undefined}
                onPluginsChanged={() => void probe.refresh()}
              />
            </Card>
          </div>
        ) : null}

        {section === "llm" ? (
          <div className="max-w-3xl">
            <Card
              title="模型连接"
              description="走 OpenAI 兼容 API（百炼 / 硅基流动 / 自建端点都行）；应用后下一轮对话即生效。"
            >
              <LlmSettings open onClose={noop} variant="inline" panelId="console-llm" />
            </Card>
          </div>
        ) : null}

        {section === "persona" ? (
          <div className="max-w-3xl">
            <Card
              title="人设与文风"
              description="这里改的是共享偏好（存后端）：Web 端与桌宠窗下次打开时按它生效。"
            >
              <PersonaPanel />
            </Card>
          </div>
        ) : null}

        {section === "memory" ? (
          <div className="max-w-3xl">
            <Card title="会话与记忆" description="四层记忆：工作记忆（会话内）/ 情景 / 语义 / 个人记忆。">
              <MemoryPanel />
            </Card>
          </div>
        ) : null}

        {section === "studio" ? (
          <div className="max-w-4xl">
            <Card title="创作工坊" description="自建角色卡、背景故事与世界书条目（内置内容只读）。">
              <StudioPanel open onClose={noop} variant="inline" panelId="console-studio" />
            </Card>
          </div>
        ) : null}

        {section === "pet" ? (
          <div className="max-w-3xl">
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
          <div className="max-w-3xl">
            <Card title="技能" description="清单常驻上下文，正文按需拉取（模型调 study_skill 时才注入）。">
              <SkillPanel
                skills={probe.status?.skills ?? []}
                onSkillsChanged={() => void probe.refresh()}
              />
            </Card>
          </div>
        ) : null}

        {!IMPLEMENTED.has(section) ? (
          <div className="max-w-3xl">
            {SECTIONS.filter((item) => item.id === section).map((item) => (
              <Card key={item.id} title={item.label} description={item.hint}>
                <p className="rounded-xl bg-surface-inset px-4 py-3 text-xs leading-relaxed text-ink-muted">
                  {item.pending ?? "待接入"}
                </p>
              </Card>
            ))}
          </div>
        ) : null}
      </main>
    </div>
  );
}
