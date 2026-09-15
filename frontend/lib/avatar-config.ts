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
