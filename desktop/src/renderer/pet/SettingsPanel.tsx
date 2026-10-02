import { useEffect, useState } from "react";

import {
  AVATAR_RENDERER_OPTIONS,
  type AvatarCredentials,
  type AvatarRendererPreference,
  type CredentialSource,
} from "@/lib/avatar/avatar-config";
import { normalizeUserName, USER_NAME_MAX_LENGTH } from "@/lib/chat/user";

import {
  MAX_PET_SCALE,
  MIN_PET_SCALE,
  PET_SCALE_STEP,
  type PetEnvironment,
  type PetWindowState,
} from "../../shared/ipc";
import { PresetPanel, type PetPresetSettings } from "./PresetPanel";
import { SessionPanel, type PetChatSettings } from "./SessionPanel";
import type { BackendHealth } from "./useBackendHealth";
import type { DesktopPerception } from "./useDesktopPerception";

/**
 * 形象相关状态（由 App 统一持有）。
 *
 * 传一个对象而不是铺平成 8 个 props：它们全都是同一件事（桌宠用什么形象），
 * 拆开后调用处会被无关字段淹没。
 */
export interface PetAvatarSettings {
  /** 渲染方式偏好（与 Web 端同一份 localStorage；控制台与桌宠窗同源，故共享） */
  renderer: AvatarRendererPreference;
  onRendererChange(value: AvatarRendererPreference): void;
  /** 魔珐凭证（`pet` = **竖屏**应用，与 Web 端用的 `web` 形态分开存） */
  credentials: AvatarCredentials | null;
  source: CredentialSource;
  save(credentials: AvatarCredentials): Promise<boolean>;
  clear(): Promise<boolean>;
  /** 魔珐降级原因（null = 没降级）；降级后仍会展示，便于现场排查 */
  degradedReason: string | null;
  /** 当前是否真的在用魔珐 3D */
  xmovActive: boolean;
  /** 首屏凭证拉取中 */
  loading: boolean;
}

export interface SettingsPanelProps {
  state: PetWindowState;
  environment: PetEnvironment | null;
  health: BackendHealth;
  avatar: PetAvatarSettings;
  /** 角色与会话（桌宠窗自己的入口；同样传一个对象，理由同 `avatar`） */
  chat: PetChatSettings;
  /** 文风 / 提示词预设 / 酒馆预设（同上） */
  presets: PetPresetSettings;
  /** 用户称呼（`{{user_name}}`）：当前值 + 保存到后端共享偏好 */
  userName: string;
  onSaveUserName(name: string): Promise<void>;
  /**
   * 桌面情景上报状态（感知层，见 `docs/proactive-multimodal.md` §4.2）。
   *
   * 放在设置面板里是因为**它必须可见**：用户在系统设置里关掉「主动沟通」之后，
   * 如果界面上没有任何迹象，他无法区分「关了」「没生效」「本来就没采集」。
   */
  perception: DesktopPerception;
  onUpdateScale(scale: number): void;
  onClose(): void;
}

/** 凭证来源文案（与 Web 端 `AvatarSettings` 的口径一致） */
const SOURCE_LABEL: Record<CredentialSource, string> = {
  user: "界面填写（存后端）",
  env: "部署配置（backend/.env）",
  none: "未配置",
};

/**
 * 行踪与偏好画像（`docs/proactive-multimodal.md` §4.9）。
 *
 * 为什么单列一块而不是塞进上面那句「它此刻知道的」：那说的是**此刻**（易失，
 * 一分钟后就没有了），这里说的是**历史**（会被保留若干天）。两者的敏感度
 * 差一个量级，用户在界面上要能分清自己在看哪一个，也才知道「清除」清的是什么。
 *
 * 三条必须做到：
 * 1. **逐段可核对**（几点到几点在用什么程序）——只说「它在记录你的行踪」，
 *    用户只能靠想象，而想象出来的总是更糟；
 * 2. **说清记了什么、没记什么**（默认不记窗口标题、不记内容、不进记忆库）；
 * 3. **一键清干净**，且清完立刻反映在界面上。
 */
function ActivityField({ perception }: { perception: DesktopPerception }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const activity = perception.activity;

  async function handleClear() {
    setBusy(true);
    setMessage(await perception.clearActivity());
    setBusy(false);
  }

  if (!activity) {
    return (
      <div className="petField">
        <span>
          行踪 <strong>未启用</strong>
        </span>
        <p className="petHint">
          未启用或后端不可达。启用后它会记住「几点到几点在用什么程序」，
          用来在合适的时机主动搭话（「忙完了？」「回来啦」）。
        </p>
      </div>
    );
  }

  return (
    <div className="petField">
      <span>
        行踪 <strong>{activity.enabled ? "记录中" : "已关闭"}</strong>
        {activity.enabled ? ` · 保留 ${activity.retention_days} 天` : ""}
      </span>

      {activity.enabled ? (
        <>
          <p className="petHint">
            {activity.current
              ? `此刻在用：${activity.current}`
              : "此刻没有采集到正在使用的程序。"}
          </p>
          {activity.profile_lines.length > 0 && (
            <p className="petHint">
              它对你的印象：
              {activity.profile_lines.join("；")}
            </p>
          )}
          {activity.lines.length > 0 ? (
            <p className="petHint">最近：{activity.lines.slice(-6).join("、")}</p>
          ) : (
            <p className="petHint">还没有积累到足够的记录。</p>
          )}
          <p className="petHint">
            只记程序名
            {activity.include_title ? "（已开启窗口标题，内容会更具体）" : "，不记窗口标题"}
            ；不记按键、不记内容，也不会写进长期记忆。
          </p>
          <button type="button" onClick={() => void handleClear()} disabled={busy}>
            {busy ? "正在清除…" : "清除行踪记录"}
          </button>
        </>
      ) : (
        <p className="petHint">
          已关闭：采集已停止，已记录的内容也已被清除。
          可在程序控制台的「能力中心 → 时间 · 行踪 · 画像」里重新开启。
        </p>
      )}

      {message && <p className="petHint">{message}</p>}
    </div>
  );
}

/** 当前形象状态的一句话说明（降级原因要看得见，否则用户只会觉得「3D 坏了」） */
function describeAvatar(avatar: PetAvatarSettings): string {
  if (avatar.xmovActive) {
    return "魔珐星云 3D 渲染中";
  }

  if (avatar.degradedReason) {
    return `已降级为本地渲染：${avatar.degradedReason}`;
  }

  if (avatar.loading) {
    return "正在读取凭证…";
  }

  if (avatar.source === "none") {
    return avatar.renderer === "xmov"
      ? "已选择魔珐，但尚未配置密钥 → 本地渲染"
      : "未配置密钥 → 本地渲染（Live2D / 立绘）";
  }

  // 部署配置里有密钥却没起 3D：必须解释清楚，否则看起来像「明明配了却不生效」
  if (avatar.source === "env" && avatar.renderer === "auto") {
    return "本地渲染（部署配置的凭证没有自动启用 3D：桌宠需竖屏应用，可在上面显式选「魔珐星云 SDK」）";
  }

  return "本地渲染（按偏好强制，未启用 3D）";
}

/**
 * 桌宠设置面板。
 *
 * 放三类东西：
 * - 桌宠窗口自身的开关（缩放 / 置顶 / 穿透状态）与运行环境信息；
 * - **形象与魔珐凭证**——凭证必须在这里能填，因为 `pet`（竖屏）是独立于 Web 端
 *   `web`（横屏）的一套，Web 端的设置面板不会写它；
 * - **对话要用的三项选择**：角色与会话、预设（文风 / 提示词 / 酒馆）、用户称呼。
 *   桌宠窗是长期开着的那一个窗口，这些如果只能去控制台改，用户就会以为
 *   「桌宠窗没法换」。它们存的都是**后端共享偏好**，所以三端读的是同一份。
 *
 * 语音、模型接入、记忆与知识库等**重型**设置仍在 Web 端与控制台——那些要配
 * 密钥、要传文件、要看检索结果，塞进一个 280×480 的小窗只会两头都做不好。
 */
/**
 * 用户称呼输入（`{{user_name}}`）。
 *
 * **为什么不直接复用 Web 端的 `UserNameField`**：桌宠入口不加载 Tailwind
 * （`pet/main.tsx` 只引入 `pet.css`，Tailwind 接在控制台入口），引入那个组件
 * 会渲染成没有样式的裸元素。所以这里用桌宠自己的 `.petField` 体系，
 * 只共用**行为**：`normalizeUserName` 与长度上限都与控制台 / Web 端同一口径。
 *
 * 存的是后端共享偏好 → 在桌宠窗改完，控制台与 Web 端下次打开也是新称呼。
 */
function UserNameSetting({
  value,
  onSave,
}: {
  value: string;
  onSave: (name: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(value);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);

  // 外部值变化（偏好拉回来 / 在控制台改过）时同步草稿
  useEffect(() => {
    setDraft(value);
  }, [value]);

  const pending = normalizeUserName(draft);
  const dirty = pending !== value;

  async function save() {
    setBusy(true);
    setNote("");
    try {
      await onSave(pending);
      setFailed(false);
      setNote(pending ? `已保存：角色以后会叫你「${pending}」` : "已清除：角色会用通用称呼叫你");
    } catch (error: unknown) {
      setFailed(true);
      setNote(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="petField">
        <span>你希望角色怎么称呼你</span>
        <input
          type="text"
          value={draft}
          maxLength={USER_NAME_MAX_LENGTH}
          spellCheck={false}
          autoComplete="off"
          disabled={busy}
          onChange={(event) => {
            setDraft(event.target.value);
            setNote("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && dirty && !busy) void save();
          }}
        />
      </label>

      <div className="petFieldRow">
        <button type="button" onClick={() => void save()} disabled={busy || !dirty}>
          {busy ? "保存中…" : "保存称呼"}
        </button>
      </div>

      {note ? (
        <p className="petHint" role={failed ? "alert" : "status"}>
          {note}
        </p>
      ) : (
        <p className="petHint">改的是你自己的称呼（角色名由人设决定）；留空保存 = 清除。</p>
      )}
    </>
  );
}

export function SettingsPanel({
  state,
  environment,
  health,
  avatar,
  chat,
  presets,
  userName,
  onSaveUserName,
  perception,
  onUpdateScale,
  onClose,
}: SettingsPanelProps) {
  const [appId, setAppId] = useState(avatar.credentials?.appId ?? "");
  const [appSecret, setAppSecret] = useState(avatar.credentials?.appSecret ?? "");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  // 凭证变化（保存 / 清除 / 后端返回）后同步到输入框：
  // 否则点完「清除」框里还留着旧值，看起来像没清掉
  useEffect(() => {
    setAppId(avatar.credentials?.appId ?? "");
    setAppSecret(avatar.credentials?.appSecret ?? "");
  }, [avatar.credentials?.appId, avatar.credentials?.appSecret]);

  const canSave = appId.trim().length > 0 && appSecret.trim().length > 0 && !busy;

  async function handleSave() {
    if (!canSave) {
      return;
    }

    setBusy(true);
    const saved = await avatar.save({ appId, appSecret });
    setBusy(false);
    setMessage(
      saved
        ? "已保存，数字人将自动重新初始化。"
        : "保存失败：后端不可达或被拒绝，请确认后端已启动。",
    );
  }

  async function handleClear() {
    setBusy(true);
    const cleared = await avatar.clear();
    setBusy(false);

    if (!cleared) {
      setMessage("清除失败：后端不可达，请稍后重试。");

      return;
    }

    setAppId("");
    setAppSecret("");
    setMessage("已清除保存的密钥（回落到部署配置）。");
  }

  const rendererHint = AVATAR_RENDERER_OPTIONS.find(
    (option) => option.value === avatar.renderer,
  )?.hint;

  return (
    <section className="petPanel" aria-label="桌宠设置">
      <header className="petPanelHeader">
        <span>桌宠设置</span>
        <button type="button" onClick={onClose} title="关闭（Esc）">
          关闭
        </button>
      </header>

      <div className="petPanelBody">
        {/* 桌面情景：让「它在不在看、看到了什么」对用户可见（§4.6 的可观测性要求） */}
        <div className="petField">
          <span>
            桌面情景{" "}
            <strong>{perception.enabled ? "已启用" : "未启用"}</strong>
          </span>
          <p className="petHint">
            {perception.error
              ? `上报失败：${perception.error}`
              : perception.enabled
                ? perception.lastPerceptionText
                  ? `它此刻知道的：${perception.lastPerceptionText.replace(/^- /gm, "").split("\n").join("；")}`
                  : "已连接，暂时没有可感知的内容。"
                : "未启用。可在程序控制台的「能力中心 → 时间 · 行踪 · 画像 / 主动沟通」里开启。"}
          </p>
        </div>

        {/* 行踪：它是**历史**，比「此刻」敏感得多，因此必须能看见、也必须能一次清掉 */}
        <ActivityField perception={perception} />

        <label className="petField">
          <span>
            大小 <strong>{Math.round(state.scale * 100)}%</strong>
          </span>
          <input
            type="range"
            min={MIN_PET_SCALE}
            max={MAX_PET_SCALE}
            step={PET_SCALE_STEP}
            value={state.scale}
            onChange={(event) => onUpdateScale(Number(event.target.value))}
          />
        </label>

        <p className="petHint">拖动桌宠可换位置；位置与大小都会被记住。</p>

        {/* ---------- 角色与会话（不打开控制台 / Web 端也能换） ---------- */}
        <SessionPanel {...chat} />

        {/* ---------- 预设（文风 / 提示词 / 酒馆；同样不必开控制台） ---------- */}
        <PresetPanel {...presets} />

        {/* ---------- 称呼（存后端共享偏好，控制台 / Web 端 / 桌宠窗同一份） ---------- */}
        <UserNameSetting value={userName} onSave={onSaveUserName} />

        {/* ---------- 形象 ---------- */}
        <label className="petField">
          <span>形象</span>
          <select
            value={avatar.renderer}
            onChange={(event) =>
              avatar.onRendererChange(event.target.value as AvatarRendererPreference)
            }
          >
            {AVATAR_RENDERER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        {rendererHint ? <p className="petHint">{rendererHint}</p> : null}
        <p className="petHint" role="status">
          当前：{describeAvatar(avatar)}
        </p>

        {/* ---------- 魔珐凭证（竖屏应用） ---------- */}
        <label className="petField">
          <span>魔珐 App ID</span>
          <input
            type="text"
            value={appId}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setAppId(event.target.value)}
          />
        </label>

        <label className="petField">
          <span>魔珐 App Secret</span>
          <input
            type="password"
            value={appSecret}
            autoComplete="off"
            onChange={(event) => setAppSecret(event.target.value)}
          />
        </label>

        <div className="petFieldRow">
          <button type="button" onClick={() => void handleSave()} disabled={!canSave}>
            保存并启用
          </button>
          <button type="button" onClick={() => void handleClear()} disabled={busy}>
            清除
          </button>
        </div>

        {message ? (
          <p className="petHint" role="status">
            {message}
          </p>
        ) : null}

        <p className="petHint">
          来源：<strong>{SOURCE_LABEL[avatar.source]}</strong>。密钥存后端
          （<code>backend/data/avatar-credentials.json</code>），控制台 / Web 端 / 桌宠窗
          共享；魔珐 SDK 在浏览器里建会话，密钥因此会下发到页面。
        </p>
        <p className="petHint">
          桌宠需要<strong>竖屏</strong>应用：横竖屏是在魔珐控制台**创建应用时**定的，
          容器比例必须与应用类型一致（Web 端用的是另一套横屏凭证）。没有竖屏应用时，
          用「本地渲染器」即可，不影响对话与语音。
        </p>

        <dl className="petFacts">
          <dt>点击穿透</dt>
          <dd>{state.clickThrough ? "已开启（右键可关闭）" : "关闭"}</dd>
          <dt>窗口置顶</dt>
          <dd>{state.alwaysOnTop ? "是" : "否"}</dd>
          <dt>后端</dt>
          <dd>
            {environment?.apiBase ?? "—"}
            {health.online === null ? " · 探测中" : health.online ? " · 在线" : " · 未连接"}
          </dd>
          <dt>版本</dt>
          <dd>
            {environment ? `${environment.appVersion} · ${environment.platform}` : "—"}
            {environment?.development ? " · 开发模式" : ""}
          </dd>
        </dl>

        {health.online === false ? (
          <p className="petWarning">
            后端未连接：对话与播报不可用。请在项目根目录执行
            <code>docker compose up -d</code>，或本地启动 FastAPI 后再试。
          </p>
        ) : null}

        <p className="petHint">
          全局快捷键 <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> 显示 / 隐藏桌宠；
          托盘图标可唤出或退出。
        </p>
      </div>
    </section>
  );
}
