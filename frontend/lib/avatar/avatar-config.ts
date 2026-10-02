/**
 * 数字人凭证与纯前端偏好。
 *
 * 凭证**存后端**（`GET|PUT /media/avatar/credentials`，见
 * `backend/app/api/media.py`），不再写 localStorage。理由与对话偏好完全相同：
 * 三个界面的 origin 不同（控制台 / 桌宠窗是 `127.0.0.1:34567`，Web 端是
 * `localhost:3000`），localStorage 天然不共享，而「用哪套密钥」必须是三处一致的
 * 事实。凭证还按形态分两套（`web` 横屏 / `pet` 竖屏）——魔珐的容器比例必须与
 * 控制台选定的应用类型一致。
 *
 * ⚠️ 存后端**不是「更安全」**：魔珐 SDK 是客户端渲染
 * （`new XmovAvatar({appSecret})`），密钥最终必然下发到浏览器。防护靠后端 CORS
 * 只放行回环地址，不要给 `CORS_ORIGINS` 设 `"*"`。
 *
 * 本模块保留的 localStorage 内容只剩两类：渲染方式 / 语音这类**纯前端偏好**
 * （各 origin 各存各的，不涉及跨界面一致性），以及旧版凭证的一次性迁移入口。
 */

export interface AvatarCredentials {
  appId: string;
  appSecret: string;
}

/** 凭证来源（用于界面展示）：界面填写 > 部署配置 > 未配置 */
export type CredentialSource = "user" | "env" | "none";

/** 旧版存凭证用的 localStorage 键（只服务于一次性迁移，迁移完这段可删） */
const LEGACY_STORAGE_KEY = "hypra.avatar.credentials";
/** 旧存储结构版本：对不上就当作没有旧数据，避免半途结构升级造成脏读 */
const LEGACY_STORAGE_VERSION = 1;

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

/**
 * 读**旧版**存在 localStorage 里的凭证。
 *
 * 只服务于一次性迁移（把用户以前填的密钥搬到后端，见 `useAvatarCredentials`）；
 * 迁移上线一段时间后，连这个函数一起删。任何异常都按「没有旧数据」处理——
 * 迁移失败不该影响正常使用。
 */
export function readLegacyStoredCredentials(): AvatarCredentials | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // 版本不匹配：结构已变，丢弃旧数据（宁可让用户重填，也不读脏值）
    if (parsed && typeof parsed === "object" && parsed.v !== LEGACY_STORAGE_VERSION) return null;
    return isComplete(parsed)
      ? { appId: parsed.appId.trim(), appSecret: parsed.appSecret.trim() }
      : null;
  } catch {
    return null;
  }
}

/** 迁移成功后清掉旧键（否则每次挂载都会把同一份旧数据再搬一次）。 */
export function clearLegacyStoredCredentials(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch (error) {
    // 隐私模式 / 存储被禁用：清不掉也不影响使用，迁移本身是幂等的
    console.warn("[HyPRA] 旧凭证清除失败：", error);
  }
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

/**
 * 播报实现：魔珐 SDK 自带 TTS / 服务端 TTS（GPT-SoVITS）/ **不出声**。
 *
 * 刻意没有「浏览器原生 TTS」这一档（Web Speech API 已移除）：
 * 浏览器语音的音色与项目人设完全对不上，默认开启只会让陪伴体验失真。
 * 因此**只有真正接入了 TTS（服务端 GPT-SoVITS 或魔珐 SDK）才出声**，
 * 否则整轮静音、只显示文字与字幕。
 */
export type AvatarVoiceSource = "xmov" | "server" | "none";

/**
 * 语音引擎偏好（用户手动指定）。
 *
 * 为什么需要它：探测结果只在**后端就绪那一刻**取一次，而演示现场会变：
 * - 探测失败（后端刚启动、请求撞上网络抖动）但服务其实可用 → 强制服务端；
 * - 不想被动降级 → 也能强制（真失败只会静音，不会改用什么系统语音）。
 *
 * 历史值 `"browser"` 已随浏览器语音一起移除：读到它按非法值处理，回落到 `auto`。
 */
export const TTS_ENGINE_PREFERENCES = ["auto", "server"] as const;

export type TtsEnginePreference = (typeof TTS_ENGINE_PREFERENCES)[number];

/** 默认：自动（后端配好服务端 TTS 就用它，否则静音） */
export const DEFAULT_TTS_ENGINE: TtsEnginePreference = "auto";

/** 面板里的选项与说明（顺序即展示顺序） */
export const TTS_ENGINE_OPTIONS: ReadonlyArray<{
  value: TtsEnginePreference;
  label: string;
  hint: string;
}> = [
  {
    value: "auto",
    label: "自动",
    hint: "后端配好服务端 TTS 就播报；没接入 TTS 时整轮静音，只显示文字",
  },
  { value: "server", label: "强制服务端 TTS", hint: "即使探测失败也试一次；真失败则静音（不改用系统语音）" },
];

/**
 * 选择播报实现。
 *
 * 抽成纯函数而不是写在页面里：优先级是**设计约束**而不是偏好，值得被单测钉住。
 *
 * 优先级：
 * 1. 魔珐 SDK 路径只能用自带 TTS——SDK 的 `speak()` 只吃 SSML、不接受外部音频，
 *    「魔珐形象 + GPT-SoVITS 声音」当前不可行（已确认的设计边界）；
 * 2. 其余情况尊重用户偏好：`server` 强制试服务端；
 * 3. `auto` → 按探测结果决定：接入 TTS 才出声，否则落到 `none`（静音）。
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
  if (preference === "server") return "server"; // 探测失败也先试一次（真失败就静音）
  return serverTtsAvailable ? "server" : "none";
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
