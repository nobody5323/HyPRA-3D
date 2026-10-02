import { useEffect, useState } from "react";

import {
  DEFAULT_SERVICE_SETTINGS,
  type ConsoleState,
  type ServiceId,
  type ServicePhase,
  type ServiceSettings,
} from "../../../shared/ipc";

/**
 * 本机服务分组（Qdrant + 后端 + Web 前端）。
 *
 * 桌面端代管这三个进程：关掉 HyPRA 时一起收掉。
 * 「外部运行中」是刻意保留的一个状态——端口上已经有服务在跑（用户自己开的），
 * 桌面端**既不重复启动也不去停它**，否则就成了抢端口、误杀别人的进程。
 *
 * 三者里只有 Web 前端**不随程序自动启动**（`autoStart` 只管前两个）：
 * 桌宠用不到它，程序一开就拉一个 Next 服务是白搭。它由两条路径按需拉起——
 * 这里的「启动」按钮，以及「模式启动 → 以 Web 模式启动」。
 */

const PHASE_LABELS: Record<ServicePhase, string> = {
  stopped: "未启动",
  starting: "启动中…",
  ready: "运行中",
  adopted: "外部运行中",
  failed: "启动失败",
};

/**
 * 状态芯片的配色。
 *
 * 与 `PHASE_LABELS` 分开：文案是给人读的，配色是给眼睛扫的——
 * 「运行中」必须是绿的、失败必须是红的，这比读文字快得多。
 */
const PHASE_CHIPS: Record<ServicePhase, string> = {
  stopped: "chip-neutral",
  starting: "chip-accent",
  ready: "chip-success",
  adopted: "chip-accent",
  failed: "chip-danger",
};

/** 只有「运行中」才让状态点呼吸；静止状态加动效只会让人以为在加载 */
const PHASE_LIVE: Record<ServicePhase, boolean> = {
  stopped: false,
  starting: true,
  ready: true,
  adopted: false,
  failed: false,
};

/** 展示顺序即启动顺序 */
const SERVICE_META: Record<ServiceId, { name: string; hint: string }> = {
  qdrant: { name: "Qdrant 向量库", hint: "记忆的温层（情景记忆），端口 6333" },
  backend: { name: "后端 FastAPI", hint: "对话 / 记忆 / 数字人驱动，端口 8000" },
  frontend: { name: "Web 前端", hint: "浏览器里的完整体验（Next），端口 3000" },
};

const SERVICE_ORDER: ServiceId[] = ["qdrant", "backend", "frontend"];

const PATH_FIELDS: { key: keyof Pick<ServiceSettings, "projectRoot" | "pythonPath" | "qdrantPath">; label: string; hint: string }[] = [
  { key: "projectRoot", label: "项目根", hint: "留空 = 自动探测（找同时含 .venv 与 _local/qdrant 的目录）" },
  { key: "pythonPath", label: "Python 解释器", hint: "留空 = 项目根/.venv/Scripts/python.exe" },
  { key: "qdrantPath", label: "Qdrant 可执行文件", hint: "留空 = 项目根/_local/qdrant/qdrant.exe" },
];

export function ServicePanel({
  state,
  bridge,
  onNotice,
}: {
  state: ConsoleState | null;
  bridge: typeof window.hyprConsole;
  /** 操作结果提示（由外层统一展示） */
  onNotice(text: string): void;
}) {
  const services = state?.services ?? [];
  const settings = state?.serviceSettings ?? DEFAULT_SERVICE_SETTINGS;
  const [busy, setBusy] = useState(false);
  const [pathDraft, setPathDraft] = useState({
    projectRoot: settings.projectRoot,
    pythonPath: settings.pythonPath,
    qdrantPath: settings.qdrantPath,
  });

  // 主进程回报的设置变化（比如从别处改了）要同步进输入框
  useEffect(() => {
    setPathDraft({
      projectRoot: settings.projectRoot,
      pythonPath: settings.pythonPath,
      qdrantPath: settings.qdrantPath,
    });
  }, [settings.projectRoot, settings.pythonPath, settings.qdrantPath]);

  const runAction = (action: (() => Promise<unknown>) | undefined, okText: string): void => {
    if (!action) {
      onNotice("桌面能力不可用（浏览器调试模式下这些动作不会生效）");

      return;
    }

    setBusy(true);
    void action()
      .then(() => onNotice(okText))
      .catch((error: unknown) =>
        onNotice(`失败：${error instanceof Error ? error.message : String(error)}`),
      )
      .finally(() => setBusy(false));
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="notice notice-info">
        <span translate="no">HyPRA</span> 退出时会把这三个进程一起收掉。
        端口上已经有服务在跑时（你自己开的）不会被接管、也不会被停止，只显示为「外部运行中」。
        Web 前端不随程序自动启动（桌宠用不到它）——点这里的「启动」，或在「模式启动」里点
        「以 Web 模式启动」时会先把它拉起来。
      </p>

      <ul className="flex flex-col gap-2">
        {SERVICE_ORDER.map((id) => {
          const service = services.find((item) => item.id === id);
          const phase: ServicePhase = service?.phase ?? "stopped";
          const meta = SERVICE_META[id];
          const running = phase === "ready" || phase === "adopted" || phase === "starting";

          return (
            <li key={id} className="rounded-xl border border-line bg-surface-inset/60 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-ink">{meta.name}</p>
                  <p className="mt-0.5 truncate text-[11px] text-ink-faint">{meta.hint}</p>
                </div>
                <span className={`chip ${PHASE_CHIPS[phase]}`}>
                  <span
                    aria-hidden="true"
                    className={`status-dot ${PHASE_LIVE[phase] ? "animate-pulse-dot" : ""}`}
                  />
                  {PHASE_LABELS[phase]}
                  {service?.pid ? <span className="tabular-nums opacity-70">PID {service.pid}</span> : null}
                </span>
              </div>

              {service?.detail ? (
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">{service.detail}</p>
              ) : null}

              <div className="mt-2.5 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy || !bridge || running}
                  onClick={() =>
                    runAction(
                      bridge ? () => bridge.startService(id) : undefined,
                      `${meta.name}已请求启动（就绪后状态会自己更新）`,
                    )
                  }
                  className="btn btn-pill btn-primary"
                >
                  启动
                </button>
                <button
                  type="button"
                  // 没有 PID 说明不是本程序起的：不去停别人的进程
                  disabled={busy || !bridge || !service?.pid}
                  onClick={() =>
                    runAction(
                      bridge ? () => bridge.stopService(id) : undefined,
                      `${meta.name}已停止`,
                    )
                  }
                  className="btn btn-pill btn-secondary"
                >
                  停止
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={settings.autoStart}
            disabled={busy || !bridge}
            onChange={(event) =>
              runAction(
                bridge ? () => bridge.updateServiceSettings({ autoStart: event.target.checked }) : undefined,
                event.target.checked ? "已开启自动启动（下次启动 HyPRA 时生效）" : "已关闭自动启动",
              )
            }
            className="accent-accent"
          />
          启动 <span translate="no">HyPRA</span> 时自动拉起向量库与后端（Web 前端按需启动）
        </label>

        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={settings.backendReload}
            disabled={busy || !bridge}
            onChange={(event) =>
              runAction(
                bridge ? () => bridge.updateServiceSettings({ backendReload: event.target.checked }) : undefined,
                event.target.checked
                  ? "后端将带 --reload 启动（改代码即时生效，下次启动生效）"
                  : "后端不再带 --reload",
              )
            }
            className="accent-accent"
          />
          后端带 <code className="rounded bg-surface-hover px-1">--reload</code>（开发用；下次启动生效）
        </label>
      </div>

      <div className="flex flex-col gap-2">
        {PATH_FIELDS.map((field) => (
          <label key={field.key} className="flex flex-col gap-1 text-[11px] text-ink-soft">
            <span>{field.label}</span>
            <input
              type="text"
              value={pathDraft[field.key]}
              onChange={(event) =>
                setPathDraft((previous) => ({ ...previous, [field.key]: event.target.value }))
              }
              placeholder={field.hint}
              spellCheck={false}
            className="rounded-xl border border-line bg-surface-inset px-3 py-1.5 font-mono text-[11px] text-ink outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-accent/60"
          />
            <span className="text-[10px] text-ink-faint">{field.hint}</span>
          </label>
        ))}

        <button
          type="button"
          disabled={busy || !bridge}
          onClick={() =>
            runAction(
              bridge ? () => bridge.updateServiceSettings(pathDraft) : undefined,
              "服务路径已保存（下次启动生效）",
            )
          }
          className="btn btn-secondary btn-pill self-start"
        >
          保存路径
        </button>
      </div>

      <p className="field-hint">
        打包后的 exe 里不含 Python、Qdrant 与 Web 前端（阶段一），所以那种形态下需要在这里把项目根指到本机仓库目录；
        Web 端另需单独部署，或在「模式启动」里填一个已经在跑的地址。
      </p>
    </div>
  );
}
