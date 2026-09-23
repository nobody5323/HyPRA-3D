/**
 * 数字人凭证的**运行时配置**（解决构建时内联的问题）。
 *
 * 背景：`NEXT_PUBLIC_*` 是**构建时**内联的，改 `.env.local` 必须重新 build，
 * 无法在演示现场临时填写密钥。
 *
 * 本模块提供三级来源（优先级从高到低）：
 *   1. **界面填写** → 存 localStorage（可在页面「数字人设置」里随时改，即时生效）
 *   2. **构建时环境变量** → NEXT_PUBLIC_XMOV_APP_ID / APP_SECRET（部署时固化）
 *   3. 都没有 → 自动降级为「浏览器原生 TTS + 占位形象」
 *
 * 安全说明：前端密钥按魔珐官方 SDK 设计需在浏览器中使用；
 * 正式上线建议由后端签发临时凭证，此处面向演示/内网场景。
 */

export interface AvatarCredentials {
  appId: string;
  appSecret: string;
}

/** 凭证来源（用于界面展示） */
export type CredentialSource = "local" | "env" | "none";

const STORAGE_KEY = "hypra.avatar.credentials";
const CHANGE_EVENT = "hypra:avatar-credentials-changed";
/** 存储结构版本：结构变更时可据此丢弃旧数据（避免半途结构升级造成的脏读） */
const STORAGE_VERSION = 1;
/**
 * 安全提示：localStorage 为**明文**存储。
 * 魔珐官方 SDK 设计上要求前端持有驱动密钥，此处面向演示/内网场景；
 * 正式上线应由后端签发短期临时凭证，不把长期密钥下发到浏览器。
 */

/** 构建时环境变量（可能为空串） */
const ENV_CREDENTIALS: AvatarCredentials = {
  appId: (process.env.NEXT_PUBLIC_XMOV_APP_ID ?? "").trim(),
  appSecret: (process.env.NEXT_PUBLIC_XMOV_APP_SECRET ?? "").trim(),
};

/** 部署时是否已提供环境变量凭证 */
export const ENV_CREDENTIALS_PRESENT = Boolean(
  ENV_CREDENTIALS.appId && ENV_CREDENTIALS.appSecret,
);

function isComplete(value: unknown): value is AvatarCredentials {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return Boolean(
    typeof record.appId === "string" &&
      record.appId.trim() &&
      typeof record.appSecret === "string" &&
      record.appSecret.trim(),
  );
}

/** 读取界面填写的凭证（SSR 环境返回 null）。 */
export function readStoredCredentials(): AvatarCredentials | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // 版本不匹配：结构已变，丢弃旧数据（宁可让用户重填，也不读脏值）
    if (parsed && typeof parsed === "object" && parsed.v !== STORAGE_VERSION) return null;
    return isComplete(parsed)
      ? { appId: parsed.appId.trim(), appSecret: parsed.appSecret.trim() }
      : null;
  } catch {
    return null;
  }
}

/** 取当前生效的凭证与来源。 */
export function getEffectiveCredentials(): {
  credentials: AvatarCredentials | null;
  source: CredentialSource;
} {
  const stored = readStoredCredentials();
  if (stored) return { credentials: stored, source: "local" };
  if (isComplete(ENV_CREDENTIALS)) return { credentials: ENV_CREDENTIALS, source: "env" };
  return { credentials: null, source: "none" };
}

/**
 * 首屏安全值（**不读 localStorage**）。
 *
 * 必须与服务端渲染保持一致，否则 SSR/CSR 内容不一致会触发 hydration 错误：
 * 服务端读不到 localStorage，若客户端首屏就读，两者渲染结果会不同。
 * 因此首屏只用环境变量，localStorage 在挂载后再读取（见 useAvatarCredentials）。
 */
export function getInitialCredentials(): {
  credentials: AvatarCredentials | null;
  source: CredentialSource;
} {
  if (isComplete(ENV_CREDENTIALS)) return { credentials: ENV_CREDENTIALS, source: "env" };
  return { credentials: null, source: "none" };
}

/**
 * 保存界面填写的凭证（并通知订阅者）。
 *
 * @returns 是否真正写入成功（隐私模式 / 存储配额满时为 false，调用方据此提示用户）
 */
export function saveCredentials(credentials: AvatarCredentials): boolean {
  if (typeof window === "undefined") return false;
  const payload: AvatarCredentials = {
    appId: credentials.appId.trim(),
    appSecret: credentials.appSecret.trim(),
  };
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ v: STORAGE_VERSION, ...payload }),
    );
  } catch (error) {
    // localStorage 可能被禁用（隐私模式）或写满：不抛错，交由调用方提示
    console.warn("[HyPRA] 凭证写入 localStorage 失败：", error);
    return false;
  }
  notifyChange();
  return true;
}

/** 清除界面填写的凭证（回落到环境变量或降级）。 */
export function clearCredentials(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    console.warn("[HyPRA] 凭证清除失败：", error);
  }
  notifyChange();
}

/** 订阅凭证变化（保存 / 清除时触发）。 */
export function subscribeCredentials(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, listener);
  return () => window.removeEventListener(CHANGE_EVENT, listener);
}

function notifyChange(): void {
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

// =============================================================
// 渲染方式偏好
// =============================================================

/**
 * 渲染方式。
 *
 * 为什么需要它：项目有**三层渲染降级链**（魔珐 SDK → Live2D → 静态立绘），
 * 但"优先魔珐"只是默认策略——本地调试 Live2D、演示时避开积分消耗、
 * 或在弱机器上只跑静态立绘，都需要能手动指定。没有这个开关时，
 * 只要填了魔珐密钥就永远走魔珐，本地渲染器根本没机会跑。
 */
export const AVATAR_RENDERER_PREFERENCES = ["auto", "local", "xmov", "static"] as const;

export type AvatarRendererPreference = (typeof AVATAR_RENDERER_PREFERENCES)[number];

/** 默认：自动（有密钥用魔珐，否则本地渲染器） */
export const DEFAULT_RENDERER_PREFERENCE: AvatarRendererPreference = "auto";

/** 面板里的选项与说明（顺序即展示顺序） */
export const AVATAR_RENDERER_OPTIONS: ReadonlyArray<{
  value: AvatarRendererPreference;
  label: string;
  hint: string;
}> = [
  { value: "auto", label: "自动", hint: "有魔珐密钥就用魔珐，否则用本地渲染器" },
  { value: "local", label: "本地渲染器", hint: "Live2D 模型（已装 Cubism SDK），否则静态立绘" },
  { value: "xmov", label: "魔珐星云 SDK", hint: "强制使用魔珐实时驱动（需要密钥）" },
  { value: "static", label: "仅静态立绘", hint: "不加载模型，最省资源" },
];

const RENDERER_STORAGE_KEY = "hypra.avatar.renderer";
const RENDERER_CHANGE_EVENT = "hypra:avatar-renderer-changed";

function isRendererPreference(value: unknown): value is AvatarRendererPreference {
  return (
    typeof value === "string" &&
    (AVATAR_RENDERER_PREFERENCES as readonly string[]).includes(value)
  );
}

/** 读取渲染方式偏好（SSR / 存储不可用时回落默认值） */
export function readRendererPreference(): AvatarRendererPreference {
  if (typeof window === "undefined") return DEFAULT_RENDERER_PREFERENCE;
  try {
    const raw = window.localStorage.getItem(RENDERER_STORAGE_KEY);
    return isRendererPreference(raw) ? raw : DEFAULT_RENDERER_PREFERENCE;
  } catch {
    return DEFAULT_RENDERER_PREFERENCE;
  }
}

/**
 * 首屏安全值：**不读 localStorage**。
 *
 * 服务端读不到 localStorage，首屏就按默认值渲染，挂载后再读真实偏好——
 * 否则 SSR/CSR 不一致会触发 hydration 错误（与凭证的处理一致）。
 */
export function getInitialRendererPreference(): AvatarRendererPreference {
  return DEFAULT_RENDERER_PREFERENCE;
}

/** 保存渲染方式偏好并通知订阅者；返回是否真的写入成功 */
export function saveRendererPreference(value: AvatarRendererPreference): boolean {
  if (typeof window === "undefined") return false;
  if (!isRendererPreference(value)) return false;
  try {
    window.localStorage.setItem(RENDERER_STORAGE_KEY, value);
  } catch (error) {
    console.warn("[HyPRA] 渲染方式写入 localStorage 失败：", error);
    return false;
  }
  window.dispatchEvent(new Event(RENDERER_CHANGE_EVENT));
  return true;
}

/** 订阅渲染方式变化 */
export function subscribeRendererPreference(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(RENDERER_CHANGE_EVENT, listener);
  return () => window.removeEventListener(RENDERER_CHANGE_EVENT, listener);
}

// =============================================================
// 播报实现选择（声音从哪来）
// =============================================================

/** 播报实现：魔珐 SDK 自带 TTS / 服务端 TTS（GPT-SoVITS）/ 浏览器原生 TTS */
export type AvatarVoiceSource = "xmov" | "server" | "browser";

/**
 * 语音引擎偏好（用户手动指定）。
 *
 * 为什么需要它：探测结果只在**后端就绪那一刻**取一次，而演示现场会变：
 * - 想省 GPU / 降延迟 → 强制浏览器语音；
 * - 探测失败（后端刚启动、请求撞上网络抖动）但服务其实可用 → 强制服务端；
 * - 不想被动降级 → 也能强制（真失败仍旧逐句降级，不会没声音）。
 */
export const TTS_ENGINE_PREFERENCES = ["auto", "server", "browser"] as const;

export type TtsEnginePreference = (typeof TTS_ENGINE_PREFERENCES)[number];

/** 默认：自动（后端配好服务端 TTS 就用它） */
export const DEFAULT_TTS_ENGINE: TtsEnginePreference = "auto";

/** 面板里的选项与说明（顺序即展示顺序） */
export const TTS_ENGINE_OPTIONS: ReadonlyArray<{
  value: TtsEnginePreference;
  label: string;
  hint: string;
}> = [
  { value: "auto", label: "自动", hint: "后端配好服务端 TTS 就用它，否则用浏览器语音" },
  { value: "server", label: "强制服务端 TTS", hint: "即使探测失败也试一次；真失败会逐句降级" },
  { value: "browser", label: "强制浏览器语音", hint: "系统自带语音，不消耗算力、响应最快" },
];

/**
 * 选择播报实现。
 *
 * 抽成纯函数而不是写在页面里：优先级是**设计约束**而不是偏好，值得被单测钉住。
 *
 * 优先级：
 * 1. 魔珐 SDK 路径只能用自带 TTS——SDK 的 `speak()` 只吃 SSML、不接受外部音频，
 *    「魔珐形象 + GPT-SoVITS 声音」当前不可行（已确认的设计边界）；
 * 2. 其余情况尊重用户偏好：`browser` 强制浏览器语音，`server` 强制试服务端；
 * 3. `auto` → 按探测结果决定，最终一定落在能出声的一侧（浏览器 TTS 是零依赖兜底）。
 */
export function resolveVoiceSource({
  xmovActive,
  serverTtsAvailable,
  preference = DEFAULT_TTS_ENGINE,
}: {
  /** 是否正在使用魔珐 SDK 渲染 */
  xmovActive: boolean;
  /** 后端是否配好了服务端 TTS（`GET /media/tts/voices` 的 server_tts && configured） */
  serverTtsAvailable: boolean;
  /** 用户偏好（缺省 auto） */
  preference?: TtsEnginePreference;
}): AvatarVoiceSource {
  if (xmovActive) return "xmov"; // 设计约束：SDK 只能用它自带 TTS
  if (preference === "browser") return "browser";
  if (preference === "server") return "server"; // 探测失败也先试一次（失败会逐句降级）
  return serverTtsAvailable ? "server" : "browser";
}

const TTS_ENGINE_STORAGE_KEY = "hypra.avatar.tts-engine";
const TTS_ENGINE_CHANGE_EVENT = "hypra:avatar-tts-engine-changed";

function isTtsEnginePreference(value: unknown): value is TtsEnginePreference {
  return (
    typeof value === "string" && (TTS_ENGINE_PREFERENCES as readonly string[]).includes(value)
  );
}

/** 读取语音引擎偏好（SSR / 存储不可用时回落默认值） */
export function readTtsEnginePreference(): TtsEnginePreference {
  if (typeof window === "undefined") return DEFAULT_TTS_ENGINE;
  try {
    const raw = window.localStorage.getItem(TTS_ENGINE_STORAGE_KEY);
    return isTtsEnginePreference(raw) ? raw : DEFAULT_TTS_ENGINE;
  } catch {
    return DEFAULT_TTS_ENGINE;
  }
}

/**
 * 首屏安全值：**不读 localStorage**（与服务端渲染保持一致，否则触发 hydration 错误）。
 * 与渲染方式偏好同一处理。
 */
export function getInitialTtsEnginePreference(): TtsEnginePreference {
  return DEFAULT_TTS_ENGINE;
}

/** 保存语音引擎偏好并通知订阅者；返回是否真的写入成功 */
export function saveTtsEnginePreference(value: TtsEnginePreference): boolean {
  if (typeof window === "undefined") return false;
  if (!isTtsEnginePreference(value)) return false;
  try {
    window.localStorage.setItem(TTS_ENGINE_STORAGE_KEY, value);
  } catch (error) {
    console.warn("[HyPRA] 语音引擎偏好写入 localStorage 失败：", error);
    return false;
  }
  window.dispatchEvent(new Event(TTS_ENGINE_CHANGE_EVENT));
  return true;
}

/** 订阅语音引擎偏好变化 */
export function subscribeTtsEnginePreference(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(TTS_ENGINE_CHANGE_EVENT, listener);
  return () => window.removeEventListener(TTS_ENGINE_CHANGE_EVENT, listener);
}

// =============================================================
// 音色偏好（服务端 TTS 用）
// =============================================================

/**
 * 选中的音色 id（**空串 = 用后端配置的默认音色**）。
 *
 * 音色本身（参考音频 + 参考文本）由后端 `backend/data/tts_voices.json` 定义，
 * 前端只记住「选了哪一个」——与模型选择同一思路。
 */
const TTS_VOICE_STORAGE_KEY = "hypra.avatar.tts-voice";
const TTS_VOICE_CHANGE_EVENT = "hypra:avatar-tts-voice-changed";

/** 读取选中音色（SSR / 存储不可用时返回空串 = 后端默认音色） */
export function readTtsVoice(): string {
  if (typeof window === "undefined") return "";
  try {
    return (window.localStorage.getItem(TTS_VOICE_STORAGE_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

/** 首屏安全值：**恒为空串**（后端默认音色），不读 localStorage */
export function getInitialTtsVoice(): string {
  return "";
}

/** 保存选中音色（空串 = 回到后端默认音色） */
export function saveTtsVoice(voice: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    const value = String(voice ?? "").trim();
    if (value) window.localStorage.setItem(TTS_VOICE_STORAGE_KEY, value);
    else window.localStorage.removeItem(TTS_VOICE_STORAGE_KEY);
  } catch (error) {
    console.warn("[HyPRA] 音色选择写入 localStorage 失败：", error);
    return false;
  }
  window.dispatchEvent(new Event(TTS_VOICE_CHANGE_EVENT));
  return true;
}

/** 订阅音色选择变化 */
export function subscribeTtsVoice(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(TTS_VOICE_CHANGE_EVENT, listener);
  return () => window.removeEventListener(TTS_VOICE_CHANGE_EVENT, listener);
}

// =============================================================
// 当前使用的数字人模型
// =============================================================

/**
 * 选中的模型 id（**空串 = 用项目内置模型**）。
 *
 * 内置模型指 `public/live2d/default`（Live2D）与 `public/portraits`（8 张占位 SVG），
 * 它们随仓库分发、不需要上传就能演示；上传的模型存在后端，
 * 所以换浏览器/清缓存也不会丢（丢的只是“选了哪一个”这个偏好）。
 */
const MODEL_STORAGE_KEY = "hypra.avatar.model";
const MODEL_CHANGE_EVENT = "hypra:avatar-model-changed";

/** 读取选中的模型 id（SSR / 存储不可用时返回空串 = 内置模型） */
export function readSelectedModelId(): string {
  if (typeof window === "undefined") return "";
  try {
    return (window.localStorage.getItem(MODEL_STORAGE_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

/**
 * 首屏安全值：**恒为空串**（内置模型）。
 *
 * 服务端读不到 localStorage，首屏必须按内置模型渲染，挂载后再切到真实选择，
 * 否则 SSR/CSR 不一致会触发 hydration 错误（与凭证、渲染方式同一处理）。
 */
export function getInitialSelectedModelId(): string {
  return "";
}

/** 保存选中的模型 id（空串 = 回到内置模型） */
export function saveSelectedModelId(modelId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    const value = String(modelId ?? "").trim();
    if (value) window.localStorage.setItem(MODEL_STORAGE_KEY, value);
    else window.localStorage.removeItem(MODEL_STORAGE_KEY);
  } catch (error) {
    console.warn("[HyPRA] 模型选择写入 localStorage 失败：", error);
    return false;
  }
  window.dispatchEvent(new Event(MODEL_CHANGE_EVENT));
  return true;
}

/** 订阅模型选择变化 */
export function subscribeSelectedModel(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(MODEL_CHANGE_EVENT, listener);
  return () => window.removeEventListener(MODEL_CHANGE_EVENT, listener);
}
