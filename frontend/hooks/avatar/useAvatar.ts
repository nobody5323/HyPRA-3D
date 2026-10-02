"use client";

/**
 * 具身状态机 + 语音播报控制器（三种实现，同一接口，可自动降级）。
 *
 * - `useSilentAvatar`：**不出声**（未接入 TTS 时的默认）——对话、字幕、情绪照常，
 *   只是没有语音；
 * - `useXmovAvatar`：魔珐具身驱动 SDK（XmovAvatar）——真实 3D 数字人渲染，
 *   声音由 SDK 自带 TTS 出；
 * - `useServerTtsAvatar`：服务端 TTS（GPT-SoVITS）——**给 Live2D / 静态立绘出真声音**，
 *   拿不到音频时**保持静音**（不再回落浏览器语音，见下）。
 *
 * ⚠️ 浏览器原生 TTS（Web Speech API）已**整体移除**：
 * 系统音色与项目人设对不上，默认开启只会让陪伴体验失真，而它又长期作为
 * 「零依赖兜底」被自动选中——结果就是「没配 TTS 也能出声，但声音不是角色」。
 * 现在规则很简单：**只有接入 TTS 才出声，否则整轮静音**（`resolveVoiceSource`）。
 *
 * 状态机（赛题明确的评分点）：
 *   idle → listen（用户输入）→ think（等待后端）→ speak（播报）→ idle
 *                       ↑                                    │
 *                       └──────── interrupt（打断）──────────┘
 *
 * 降级策略（赛题「稳定性与容错」评分点）：
 *   未配置密钥 / SDK 脚本加载失败 / init 失败 → 自动回退**静默**，并**完整暴露原因**
 *   （stage/detail 会显示在数字人区域，便于现场排查）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import { postAvatar, resolveMediaUrl } from "@/lib/api/client";
import type { AvatarCredentials } from "@/lib/avatar/avatar-config";
import { AVATAR_STATE_LABELS, type AvatarState, type VisemeFrame } from "@/lib/api/types";

/** 数字人初始化阶段（用于向用户暴露诊断信息） */
export type AvatarInitStage =
  | "unconfigured" // 未配置凭证
  | "loading-sdk" // 正在加载 SDK 脚本
  | "initializing" // SDK 连接/渲染初始化中
  | "reconnecting" // 连接中断，退避重连中
  | "ready" // 就绪
  | "failed"; // 失败（见 detail）

/**
 * 分段播报的一段。
 *
 * text 与 ssml 成对：SSML 由后端生成（含 XML 转义与 KA 事件结构），
 * 前端**不得**自行拼标签——字幕用 text，SDK 播报用 ssml。
 */
export interface SpeechChunk {
  /** 纯文本（字幕用） */
  text: string;
  /** SSML（SDK speak 用） */
  ssml: string;
}

/**
 * 播报上下文：**本轮**的情绪（服务端 TTS 还需要它换后端产出的表情时间轴）。
 *
 * 为什么按「调用时传入」而不是 hook 参数：情绪是本轮对话的**产物**
 * （`useChatSession` 拿到回复后才知道），而 hook 在 `useChatSession` 之前调用，
 * 用 hook 参数会形成循环依赖。只有服务端 TTS 会用它，
 * 魔珐 SDK 有别的情绪通道（SSML 里已含 KA 动作），直接忽略。
 */
export interface SpeakContext {
  /** 情绪标签（英文，如 anxious） */
  emotion?: string | null;
  /** 情绪强度 0-1 */
  intensity?: number;
  /** 本轮临时覆盖音色 id（一般用设置里的默认音色，不用传） */
  voice?: string | null;
}

export interface AvatarController {
  state: AvatarState;
  stateLabel: string;
  /**
   * 控制器是否可用（**不是**「有没有声音」）。
   *
   * 静默实现同样算可用：`speak` 调用不会出错，只是不出声。
   * 「有没有声音」看 `provider`（`none` = 没接入 TTS）。
   */
  ready: boolean;
  /** 语音来源：none（不出声）/ xmov（魔珐 SDK 自带）/ server（服务端 TTS） */
  provider: "none" | "xmov" | "server";
  /** 初始化阶段（诊断用） */
  stage: AvatarInitStage;
  /** 阶段详情 / 失败原因（诊断用） */
  detail: string;
  setState: (state: AvatarState) => void;
  /**
   * 与**当前播放的音频同源**的口型时间轴（仅服务端 TTS 路径提供）。
   *
   * 为什么不另发一次请求取时间轴：音频与口型必须来自同一次合成，
   * 否则「声音 A + 口型 B」会结构性错位（浏览啧 TTS + 后端估算口型就是这个问题）。
   * 上层应优先用它（拿到就用，用不到再走独立的 `useLipSyncTimeline`）。
   */
  timeline?: readonly VisemeFrame[] | null;
  /**
   * 播报一段回复。
   * @param text 纯文本（字幕 / 服务端 TTS 用）
   * @param ssml SSML（魔珐 SDK 用；含 KA 动作指令）
   * @param context 本轮情绪等上下文（仅服务端 TTS 使用，其余实现忽略）
   */
  speak: (text: string, ssml?: string, context?: SpeakContext) => Promise<void>;
  /**
   * 分段播报（逐段产出，首段更早出声；字幕随语音推进）。
   *
   * 两种实现的差别值得记住：
   * - **服务端 TTS**：每段独立合成，边播边预取下一段（流水线），首段 2~3s 出声、段间无缝，
   *   字幕在**该段音频就绪那一刻**切换（字与声同时）；
   * - **魔珐 SDK**：段间要经 interactive_idle 过渡（官方约束 speak 不可连续调用），
   *   所以段间有约 400ms 间隔。
   *
   * @param chunks 逐段内容（纯文本给字幕、SSML 给 SDK）
   * @param onChunk 每段**开始播报时**的回调（推进字幕）
   * @param context 本轮情绪等上下文（仅服务端 TTS 使用）
   */
  speakChunks: (
    chunks: SpeechChunk[],
    onChunk?: (index: number) => void,
    context?: SpeakContext,
  ) => Promise<void>;
  /** 打断当前播报（客户端即时打断，不等服务端） */
  interrupt: () => void;
  /** SDK 挂载容器 id（仅 xmov 需要） */
  containerId?: string;
}

/** 魔珐 SDK 脚本地址（版本由官方 latest 维护） */
const XMOV_SDK_URL =
  "https://media.xingyun3d.com/xingyun3d/general/litesdk/xmovAvatar@latest.js";
const XMOV_GATEWAY = "https://nebula-agent.xingyun3d.com/user/v1/ttsa/session";

/** 初始化超时（毫秒）：资源下载或 socket.io 连接卡住时兜底，避免永久“初始化中” */
const INIT_TIMEOUT_MS = 90_000;

/** SDK 脚本加载超时（毫秒）：CDN 被拦截/断网时必须 settle，否则降级链路不触发 */
const SDK_LOAD_TIMEOUT_MS = 20_000;

/** 已失败脚本的标记属性（见 loadXmovSdk 注释） */
const SDK_FAILED_ATTR = "data-hypra-failed";

/**
 * 播报状态切换的等待时长（毫秒）。
 *
 * interactive_idle 的状态切换经 WebSocket 下发，立即 speak 会被丢弃，
 * 因此「抢占播报」与「分段播报的段间过渡」都需要留出这段时间。
 */
const STATE_SWITCH_DELAY_MS = 400;

/**
 * 播报等待超时（仅作为 voice_end 丢失时的**兜底**）。
 *
 * 原先按「每字 250ms、最小 8s」估算，对中文 TTS 偏紧：稍慢一点就被判超时，
 * 而超时收尾后调用方紧接着切回待机（`idle()` 内部会 interrupt 渲染调度），
 * 于是尾音被削掉——表现为「最后一句说不完」。现放宽到每字 400ms / 最小 12s / 上限 90s。
 */
const SPEAK_TIMEOUT_PER_CHAR_MS = 400;
const SPEAK_TIMEOUT_MIN_MS = 12_000;
const SPEAK_TIMEOUT_MAX_MS = 90_000;

/**
 * 播报收尾缓冲（毫秒，仅魔珐 SDK 路径）。
 *
 * voice_end 之后仍可能有极短的尾音/渲染收尾；调用方紧接着就切回待机，
 * 留一点缓冲避免把最后一句的尾巴切掉。
 */
const SPEAK_TAIL_MS = 600;

/** 按文本长度估算的播报等待上限（兜底用） */
function speakTimeoutMs(textLength: number): number {
  return Math.min(
    SPEAK_TIMEOUT_MAX_MS,
    Math.max(SPEAK_TIMEOUT_MIN_MS, textLength * SPEAK_TIMEOUT_PER_CHAR_MS),
  );
}

/** 断线自动重连的最大次数（超过后降级为静默，由用户手动重试） */
const MAX_RECONNECT_ATTEMPTS = 3;

/** 重连退避基数（毫秒）：1s → 2s → 4s */
const RECONNECT_BASE_MS = 1_000;

/** 重连退避上限（毫秒） */
const RECONNECT_MAX_MS = 8_000;

// =============================================================
// 实现一：不出声（未接入 TTS 时的默认）
// =============================================================

/**
 * 静默控制器：**没有任何语音**，只维护具身状态机。
 *
 * 什么时候用它：既没走魔珐 SDK、后端也没配好服务端 TTS（`resolveVoiceSource`
 * 返回 `"none"`）。对话、字幕、情绪、记忆全部照常，只是不播报——
 * 这正是「默认不使用浏览器语音，只有接入 TTS 才出声」的落点。
 *
 * 为什么仍然要实现 speak / speakChunks 而不是让调用方判空：调用方
 * （`useChatSession`）只认 `AvatarController` 这一个接口。接口保持一致，
 * 「有没有声音」这件事就只在选择层（`resolveVoiceSource`）决定，
 * 不会散落成一堆 `if (有没有语音)`。
 */
export function useSilentAvatar(): AvatarController {
  const [state, setState] = useState<AvatarState>("idle");

  /** 静默：立即返回（调用方据此把状态打回 idle） */
  const speak = useCallback(async () => {}, []);
  /** 静默：逐段推进字幕，但不出声（无音频，段间也不需要等待） */
  const speakChunks = useCallback(
    async (chunks: SpeechChunk[], onChunk?: (index: number) => void) => {
      for (let index = 0; index < chunks.length; index += 1) onChunk?.(index);
    },
    [],
  );
  const interrupt = useCallback(() => setState("idle"), []);

  return {
    state,
    stateLabel: AVATAR_STATE_LABELS[state],
    ready: true, // 接口可用（不出声是 provider="none" 的表达，不是「不可用」）
    provider: "none",
    stage: "unconfigured",
    detail: "未接入 TTS：本轮只显示文字，不播报语音",
    setState,
    speak,
    speakChunks,
    interrupt,
  };
}

// =============================================================
// 实现二：魔珐具身驱动 SDK（真实 3D 数字人）
// =============================================================

interface UseXmovOptions {
  /** 魔珐凭证（运行时配置：界面填写 > 构建时环境变量）；为空时不加载 SDK */
  credentials: AvatarCredentials | null;
  /** 是否启用（false 时不加载 SDK，用于自动降级） */
  enabled?: boolean;
  /** 配置修订号：变化即重建（支持「重新连接」，即使凭证内容未变） */
  revision?: number;
  /** 渲染容器 ref（优先使用，避免选择器解析问题） */
  containerRef?: RefObject<HTMLElement | null>;
  /** 不可用时回调（调用方据此回退静默实现） */
  onUnavailable?: (reason: string) => void;
}

/** 动态加载 SDK 脚本（幂等 + 可重试 + 超时兜底）。
 *
 * 两个必须处理的失败模式（否则重连后界面永久卡在「正在加载 SDK 脚本…」，且不会降级）：
 * 1. 浏览器不会对**同一 src 的已失败脚本**重新发起请求，且其 load/error 事件早已触发完毕——
 *    对它继续 addEventListener 永远不会回调 → 必须标记失败并在下次调用时移除重建；
 * 2. 断网/CDN 被拦截时事件可能永远不来 → 必须超时 reject，让上层走 catch → onUnavailable → 降级。
 */
function loadXmovSdk(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined") return reject(new Error("非浏览器环境"));
    if ((window as any).XmovAvatar) return resolve();

    let scriptTag: HTMLScriptElement | null = null;
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    /** 统一收尾：保证只 settle 一次，并清掉超时定时器 */
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      callback();
    };

    timer = setTimeout(() => {
      scriptTag?.setAttribute(SDK_FAILED_ATTR, "1"); // 标记失败：下次调用可重建
      finish(() => reject(new Error(`SDK 脚本加载超时（${SDK_LOAD_TIMEOUT_MS / 1000}s）`)));
    }, SDK_LOAD_TIMEOUT_MS);

    const existing = document.querySelector<HTMLScriptElement>(`script[src="${XMOV_SDK_URL}"]`);
    if (existing && existing.getAttribute(SDK_FAILED_ATTR) !== "1") {
      scriptTag = existing;
      existing.addEventListener("load", () => finish(() => resolve()));
      existing.addEventListener("error", () => {
        existing.setAttribute(SDK_FAILED_ATTR, "1");
        finish(() => reject(new Error("SDK 脚本加载失败")));
      });
      return;
    }
    // 已失败的旧脚本：移除后重建，浏览器才会重新发起请求
    existing?.remove();

    const script = document.createElement("script");
    script.src = XMOV_SDK_URL;
    script.async = true;
    scriptTag = script;
    script.onload = () => finish(() => resolve());
    script.onerror = () => {
      script.setAttribute(SDK_FAILED_ATTR, "1");
      finish(() => reject(new Error("SDK 脚本加载失败（网络或域名被拦截）")));
    };
    document.head.appendChild(script);
  });
}

/** 把 SDK 抛出的任意错误转成可读文本（便于现场排查） */
function describeError(error: unknown): string {
  if (!error) return "未知错误";
  const err = error as any;
  const parts: string[] = [];
  if (err.name) parts.push(String(err.name));
  if (err.message) parts.push(String(err.message));
  if (typeof err === "string") parts.push(err);
  if (err.code) parts.push(`code=${err.code}`);
  return parts.length ? parts.join(": ") : JSON.stringify(err).slice(0, 200);
}

export function useXmovAvatar(
  containerId = "#avatar-container",
  options: UseXmovOptions,
): AvatarController {
  const { credentials, enabled = true, revision = 0, containerRef, onUnavailable } = options;
  const [state, setState] = useState<AvatarState>("idle");
  const [ready, setReady] = useState(false);
  const [stage, setStage] = useState<AvatarInitStage>("unconfigured");
  const [detail, setDetail] = useState("");
  const avatarRef = useRef<any>(null);
  /** 同步跟踪具身状态（供 speak 判断是否需要先切回待机） */
  const stateRef = useRef<AvatarState>("idle");
  /** 当前播报的收尾回调：由 SDK 的 voice_end 触发，让 speak() 的 Promise 正确结束 */
  const finishSpeakRef = useRef<(() => void) | null>(null);
  /** 组件是否已卸载（异步等待结束后据此放弃后续操作） */
  const unmountedRef = useRef(false);
  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
    };
  }, []);

  /**
   * 断线重连：token 变化触发 effect 重建 SDK（与凭证变更走同一条重建路径）。
   * 用指数退避而非立即重建：断网瞬间连续重试只会连续失败，还会刷屏错误提示。
   */
  const [reconnectToken, setReconnectToken] = useState(0);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * 播报代次：每次新播报或打断都自增。
   *
   * 分段循环据此判断「是否已被打断 / 被取代」——**不能用 stateRef 判断**：
   * 每段正常播完时 voice_end 会把状态置回 idle，与「被打断」无法区分。
   */
  const speakGenerationRef = useRef(0);

  const updateState = useCallback((next: AvatarState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  // 凭证内容变化 → 重建 SDK（用字符串做依赖，避免对象引用每次变化）
  const credentialKey = credentials
    ? `${credentials.appId}:${credentials.appSecret}`
    : "";

  // 凭证 / revision 变化（用户重新保存密钥）→ 重连预算复位。
  // 刻意**不含** reconnectToken：内部退避重连不应重置预算，否则会变成无限重连。
  useEffect(() => {
    reconnectAttemptRef.current = 0;
  }, [credentialKey, revision]);

  useEffect(() => {
    if (!enabled) return;

    if (!credentials) {
      setStage("unconfigured");
      setDetail("未配置魔珐密钥（点击右上角「配置数字人密钥」填写）");
      onUnavailable?.("未配置魔珐密钥");
      return;
    }

    let disposed = false;

    (async () => {
      // 提到 try 外：失败时需销毁实例（超时/鉴权失败等）
      let avatar: any = null;

      /**
       * 排一次退避重连；返回 true 表示已排程（本次不降级）。
       *
       * 必须定义在 try **之外**：`onError` 与「init/脚本加载抛异常」两条路径
       * 共用它，否则重连过程中初始化超时或脚本加载失败会直接永久降级，
       * 重连预算形同虚设。
       */
      const scheduleReconnect = (reason: string): boolean => {
        const attempt = reconnectAttemptRef.current;
        if (attempt >= MAX_RECONNECT_ATTEMPTS) return false;
        reconnectAttemptRef.current = attempt + 1;
        const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt);
        console.warn(`[HyPRA][avatar] ${reason}；${delay}ms 后第 ${attempt + 1} 次重连`);
        setStage("reconnecting");
        setDetail(
          `数字人连接中断：${reason}。${Math.round(delay / 1000)} 秒后自动重连` +
            `（第 ${attempt + 1}/${MAX_RECONNECT_ATTEMPTS} 次）`,
        );
        setReady(false);
        if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = setTimeout(
          () => setReconnectToken((count) => count + 1),
          delay,
        );
        return true;
      };

      try {
        setStage("loading-sdk");
        setDetail(`正在加载 SDK 脚本…（${XMOV_SDK_URL.split("/").pop()}）`);
        await loadXmovSdk();
        if (disposed) return;

        setStage("initializing");
        setDetail("SDK 已加载，正在建立数字人连接…");

        // 官方 SDK 要求 containerId 为 **CSS 选择器**（内部用 document.querySelector）；
        // 同时优先传 HTMLElement，避免选择器解析失败。
        const element = containerRef?.current ?? null;
        // SDK 失败时 init() 仍会 resolve（如容器不存在），因此用标志记录 onMessage 报错
        let initError: string | null = null;

        // 构造参数：
        // - containerId / container：容器定位（选择器兜底 + 元素优先）
        // - 回调：官方列为初始化参数，且 SDK 内部**无条件调用 onDownloadProgress**，
        //   缺失会抛 TypeError（实测错误：Cannot read properties of undefined 'onDownloadProgress'）
        // 进度处理统一走这里：① 消除「构造参数」与「init 参数」各写一份的重复；
        // ② 每 5% 才 setState 一次，避免加载期间整页高频重渲染
        let lastReportedProgress = -5;
        const reportProgress = (progress: number) => {
          const pct = Math.round(progress);
          if (pct >= 99) {
            // 资源已就绪：即使 init 的 Promise 仍在等会话握手，界面也先转为可用
            setStage("ready");
            setReady(true);
            setDetail("魔珐数字人已就绪");
            return;
          }
          if (pct - lastReportedProgress < 5) return;
          lastReportedProgress = pct;
          setStage("initializing");
          setDetail(`正在加载数字人资源… ${pct}%`);
        };

        const config: Record<string, unknown> = {
          containerId,
          appId: credentials.appId,
          appSecret: credentials.appSecret,
          gatewayServer: XMOV_GATEWAY,
          hardwareAcceleration: "prefer-hardware",

          /** 资源加载进度（必需参数）：达到 100% 即视为就绪，不阻塞在 init 的 Promise 上 */
          onDownloadProgress: reportProgress,
          /** SDK 状态 → 具身状态机（speak / idle 等） */
          onStateChange: (sdkState: string) => {
            // 必须走 updateState：它同步维护 stateRef，
            // 否则 speak() 的「是否正在播报」判断会读到过期值
            const value = String(sdkState ?? "").toLowerCase();
            if (value.includes("speak") || value.includes("play")) updateState("speak");
            else if (value.includes("listen")) updateState("listen");
            else if (value.includes("idle")) updateState("idle");
          },
          /** SDK 消息 / 错误（错误通过 code 字段区分） */
          onMessage: (payload: any) => {
            if (payload && payload.code !== undefined) {
              const reason = `${payload.message ?? "SDK 报错"}（code=${payload.code}）`;
              initError = reason;
              setStage("failed");
              setDetail(reason);
              setReady(false);
              onUnavailable?.(reason);
            }
          },
          /** 以下回调官方示例均提供，这里仅占位避免 SDK 内部空引用 */
          onNetworkInfo: () => {},
          onStatusChange: () => {},
          onStateRenderChange: () => {},
          onWidgetEvent: () => {},
        };
        if (element) config.container = element;

        const avatarInstance = new (window as any).XmovAvatar(config);
        avatar = avatarInstance;
        // ⚠️ 提前挂载引用：init() 可能因会话握手较慢而长时间未 resolve，
        // 但渲染与播报已可用——否则 speak() 会因 avatarRef 为空而静默失效（没声音）。
        avatarRef.current = avatarInstance;

        // 语音状态 → 驱动具身状态机（voice_end 后回到交互待机）
        //
        // ⚠️ SDK 回调签名为 `onVoiceStateChange(state, duration, client_speak_id)`（源码
        // EventDispatcher 的 `voice_state_change` 分支），而 `state` 的**实际取值是
        // `"start"` / `"end"`**——官方文档里用事件名（voice_start / voice_end）描述，
        // 但回调参数不是这两个字符串。
        //
        // 这里两种写法都接受：若只认 "voice_end"，SDK 回的 "end" 永远匹配不上，
        // 每段播报都只能靠超时收尾 → 最后一段被紧随其后的 idle() 切断（实测问题）。
        const handleVoiceState = (...args: unknown[]) => {
          const first = args[0];
          const raw = typeof first === "string" ? first : (first as { state?: unknown } | null)?.state;
          const state = String(raw ?? "").toLowerCase();
          const speaking = state === "voice_start" || state === "start";
          const finished = state === "voice_end" || state === "end";
          console.log(
            "[HyPRA][avatar] 语音状态:",
            JSON.stringify(raw),
            "| duration:",
            args[1],
            "| speakId:",
            args[2],
          );
          if (speaking) updateState("speak");
          if (finished) {
            avatar?.interactiveidle?.(); // SDK 公开方法（小写无下划线）
            updateState("idle");
            // 播报结束 → resolve speak()，使 xmov 与另两个实现的 await 语义一致
            finishSpeakRef.current?.();
          }
        };
        avatar.onVoiceStateChange = handleVoiceState;

        // SDK 内部错误也暴露出来（否则只会静默失败）
        //
        // 断线（WebSocket 断开 / 网关重启）走**指数退避自动重连**，而不是立刻永久降级：
        // 演示现场网络抖动或服务重启后，界面应当自己恢复；
        // 连续失败超过上限才降级为静默，并保留失败原因供现场排查。
        avatar.onError = (error: unknown) => {
          // 旧实例（已 destroy）的迟到回调：不得再排重连或改界面状态
          if (disposed) return;
          const reason = describeError(error);
          // 未超出上限 → 指数退避重连（评审现场拔网线插回、魔珐服务抖动都能自恢）
          if (scheduleReconnect(reason)) return;
          // 重连用尽 → 降级为静默（页面保留失败原因供现场排查）
          setStage("failed");
          setDetail(
            `SDK 运行错误（已自动重连 ${MAX_RECONNECT_ATTEMPTS} 次未成功）：${reason}`,
          );
          setReady(false);
          onUnavailable?.(reason);
        };

        // ⚠️ 官方文档「1.3 初始化连接房间」参数表明确：onDownloadProgress 为**必填**
        // （init 的参数，非构造参数）；源码中为无条件调用，缺失即抛 TypeError。
        //
        // 额外加超时保护：资源下载或 socket.io 连接卡住时，避免永久停在“初始化中”。
        let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
        const timeoutPromise = new Promise<never>((_, reject) => {
          timeoutHandle = setTimeout(
            () =>
              reject(
                new Error(
                  `初始化超时（${INIT_TIMEOUT_MS / 1000}s）：资源加载或服务连接未完成`,
                ),
              ),
            INIT_TIMEOUT_MS,
          );
        });

        try {
          await Promise.race([
            avatar.init({
              // 与构造参数共用同一进度处理（同样每 5% 才刷新一次界面）
              onDownloadProgress: reportProgress,
              initModel: "normal",
            }),
            timeoutPromise,
          ]);
        } finally {
          if (timeoutHandle) clearTimeout(timeoutHandle);
        }
        if (initError) return; // 初始化已失败并在 onMessage 中上报（init 仍会 resolve）
        if (disposed) {
          avatar.destroy?.();
          return;
        }

        avatarRef.current = avatar;
        reconnectAttemptRef.current = 0; // 连上了：重连计数归零
        if (reconnectTimerRef.current) {
          // 同时清掉「init 期间已排程」的重连：否则它到期后会把刚就绪的实例
          // 销毁重建（正在进行的播报被切断）
          clearTimeout(reconnectTimerRef.current);
          reconnectTimerRef.current = null;
        }
        setReady(true);
        setStage("ready");
        setDetail("魔珐数字人已就绪");
      } catch (error) {
        if (disposed) return; // 卸载造成的失败：不重连、不降级
        const reason = describeError(error);
        avatar?.destroy?.(); // 先清理失败实例（避免 WebGL 资源泄漏）
        if (scheduleReconnect(`初始化失败：${reason}`)) return;
        setStage("failed");
        setDetail(
          `数字人初始化失败（已自动重连 ${MAX_RECONNECT_ATTEMPTS} 次未成功）：${reason}`,
        );
        setReady(false);
        onUnavailable?.(reason);
      }
    })();

    return () => {
      disposed = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
      avatarRef.current?.destroy?.(); // 官方要求：卸载前销毁，释放 WebGL 资源
      avatarRef.current = null;
    };
    // containerId 或凭证变化时重建（其余依赖刻意不入列，避免重复初始化）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [containerId, enabled, credentialKey, revision, reconnectToken]);

  /** 切换具身状态：同步更新 React 状态与 SDK 行为状态 */
  const setAvatarState = useCallback(
    (next: AvatarState) => {
      updateState(next);
      const avatar = avatarRef.current;
      if (!avatar) return;
      // 官方公开方法：idle() / listen() / interactiveidle()
      if (next === "idle") avatar.idle?.();
      else if (next === "listen") avatar.listen?.();
      else if (next === "think") avatar.interactiveidle?.();
    },
    [updateState],
  );

  /**
   * 调用一次 SDK speak 并等待 voice_end（含超时兜底）。
   *
   * 等 voice_end 再返回：原先直接 fire-and-forget，调用方 `await speak()` 会立刻
   * 继续并把「说话中」打回 idle（与另两个实现语义不一致，字幕/徽标提前回落）。
   */
  const speakOnce = useCallback(async (payload: string, textLength: number) => {
    const avatar = avatarRef.current;
    if (!avatar) return;

    // 超时按文本长度估算兜底，保证 voice_end 丢失时也不会永久挂起。
    const timeoutMs = speakTimeoutMs(textLength);
    await new Promise<void>((resolve) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = () => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        if (finishSpeakRef.current === finish) finishSpeakRef.current = null;
        resolve();
      };
      timer = setTimeout(() => {
        console.warn(`[HyPRA][avatar] 未收到 voice_end，${timeoutMs}ms 后按超时收尾`);
        finish();
      }, timeoutMs);
      finishSpeakRef.current = finish;
      try {
        avatar.speak(payload, true, true);
      } catch (error) {
        console.warn("[HyPRA][avatar] speak 抛错:", describeError(error));
        finish();
      }
    });
  }, []);

  const speak = useCallback(
    async (text: string, ssml?: string) => {
      const avatar = avatarRef.current;
      if (!avatar) return;
      speakGenerationRef.current += 1; // 抢占：作废进行中的分段循环

      // 官方约束：speak 不允许连续调用，需先用 interactive_idle 做状态切换；
      // 而状态切换是异步的（经 WebSocket 下发），立即 speak 会被忽略——
      // 因此仅在「正在播报」时打断并等待片刻，其余情况直接播报。
      if (stateRef.current === "speak") {
        console.log("[HyPRA][avatar] 正在播报，先打断并切回交互待机");
        avatar.interrupt?.();
        avatar.interactiveidle?.();
        await new Promise((resolve) => setTimeout(resolve, STATE_SWITCH_DELAY_MS));
        if (unmountedRef.current) return; // 等待期间组件已卸载
        // 等待期间用户已打断（stateRef 被 interrupt 置回 idle）→ 不再开始播报
        if (stateRef.current !== "speak") {
          console.log("[HyPRA][avatar] 等待期间已打断，跳过本次播报");
          return;
        }
      }

      console.log("[HyPRA][avatar] speak 调用:", (ssml || text).slice(0, 60));
      await speakOnce(ssml || text, text.length);
      // 尾音缓冲：调用方紧接着会切回待机，避免切掉最后一句的尾巴
      await new Promise((resolve) => setTimeout(resolve, SPEAK_TAIL_MS));
    },
    [speakOnce],
  );

  /**
   * 分段播报（魔珐 SDK）。
   *
   * 与整段播报的差别：首段不必等整段 TTS 合成即可出声，字幕也能随段推进；
   * 代价是段间存在过渡间隔（SDK 不允许连续 speak）。
   * 因此默认关闭，由界面上的「分段播报」开关控制。
   */
  const speakChunks = useCallback(
    async (chunks: SpeechChunk[], onChunk?: (index: number) => void) => {
      const avatar = avatarRef.current;
      if (!avatar || chunks.length === 0) return;
      // 本次分段播报的代次：段间据此判断是否已被打断 / 被新播报取代。
      // ⚠️ 不能用 stateRef 判断：每段正常播完时 voice_end 会把状态置回 idle，
      // 与「被打断」在 state 上无法区分（曾导致只播第一段就 return）。
      const generation = ++speakGenerationRef.current;

      for (let index = 0; index < chunks.length; index += 1) {
        if (unmountedRef.current || generation !== speakGenerationRef.current) return;
        onChunk?.(index);
        if (index > 0) {
          avatar.interactiveidle?.();
          await new Promise((resolve) => setTimeout(resolve, STATE_SWITCH_DELAY_MS));
          if (unmountedRef.current || generation !== speakGenerationRef.current) return;
        }
        await speakOnce(chunks[index].ssml, chunks[index].text.length);
      }
      // 全部播完后再留一点尾音缓冲：最后一段之后紧接着就是「切回待机」，
      // 不留缓冲会把最后一句的尾巴切掉（用户实测的「最后一段说不完」）
      await new Promise((resolve) => setTimeout(resolve, SPEAK_TAIL_MS));
    },
    [speakOnce],
  );

  const interrupt = useCallback(() => {
    speakGenerationRef.current += 1; // 作废分段循环
    const avatar = avatarRef.current;
    avatar?.interrupt?.();
    avatar?.interactiveidle?.();
    updateState("idle");
    // 立即结束正在等待 voice_end 的 speak()/speakChunks()：
    // 被打断时 SDK 通常不再发 voice_end，否则要空等最长达 60s 的超时兜底
    finishSpeakRef.current?.();
  }, [updateState]);

  return {
    state,
    stateLabel: AVATAR_STATE_LABELS[state],
    ready,
    provider: "xmov",
    stage,
    detail,
    setState: setAvatarState,
    speak,
    speakChunks,
    interrupt,
    containerId,
  };
}

// =============================================================
// 实现三：服务端 TTS（GPT-SoVITS）—— 给 Live2D / 静态立绘出真声音
// =============================================================

/**
 * 播放收尾的宽限时间（在音频时长之上再等这么久）。
 *
 * `ended` 在少数情况下不会触发（解码中途失败、元素被换源、标签页被冻结），
 * 没有兜底就会永远卡在 speak，调用方再也回不到 idle。
 */
const AUDIO_END_GRACE_MS = 5_000;

/** 播放等待上限：优先按音频真实时长，拿不到时长时按文本长度估。 */
function audioTimeoutMs(durationMs: number, textLength: number): number {
  return durationMs > 0 ? durationMs + AUDIO_END_GRACE_MS : speakTimeoutMs(textLength);
}

/**
 * 流水线预取深度（领先当前播报段多少段）。
 *
 * **实测结论：深度 1 最优，更深反而更慢**（96 字长回复、7 段）：
 * - 深度 1 → 首段 **1.66s** 出声，之后每段都在上一段播完前就绪，全程无缝；
 * - 深度 2 → 首段反而拖到 **4.60s**：服务端把并发的两个请求一起处理、几乎同时返回，
 *   等于把第一段拖成"两段的合成时间"。更深还额外占用一个后端事件循环。
 *
 * 深度 1 就够了的原因：首段之后每段的播放时长（2~4.5s）都大于下一段的合成耗时（1.5~2.6s），
 * 播放期间足够把下一段取回来。
 */
const PREFETCH_DEPTH = 1;

/** 一段**已取回**的服务端音频（音频地址 + 与它同源的口型时间轴） */
interface FetchedSegment {
  audioUrl: string;
  visemes: readonly VisemeFrame[] | null;
  durationMs: number;
  textLength: number;
}

/**
 * `playSegmentAudio` 的结局。
 *
 * `blocked`（自动播放被拦）**不降级**：拦得住它就意味着用户还没交互，
 * 再降级（改用别的语音）只会双重出声或白等一次超时，所以整轮静音收尾。
 */
type SegmentPlayOutcome = "played" | "blocked";

/**
 * 等音频收尾：`ended` / `error` / 超时 / **被中断** 四条路径都必须 settle。
 *
 * 为什么必须监听 interrupt：暂停音频**不会**触发 `ended`，若只靠 `ended` +
 * 超时，一次打断就会让这个 Promise 挂住到最长 95s（旧路径踩过这个坑，
 * 调用方再也回不到 idle）。所以中断时用同一个 AbortSignal 把它收尾。
 */
function waitAudioFinished(
  audio: HTMLAudioElement,
  timeoutMs: number,
  signal: AbortSignal,
): Promise<"ended" | "error" | "timeout" | "cancelled"> {
  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      if (timer) clearTimeout(timer);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("error", onError);
      signal.removeEventListener("abort", onAbort);
    };
    const finish = (reason: "ended" | "error" | "timeout" | "cancelled") => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(reason);
    };
    function onEnded() {
      finish("ended");
    }
    function onError() {
      finish("error");
    }
    function onAbort() {
      finish("cancelled");
    }

    timer = setTimeout(() => finish("timeout"), timeoutMs);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("error", onError);
    signal.addEventListener("abort", onAbort);

    // 信号在监听前就已中止（打断发生在请求期间）：立即收尾
    if (signal.aborted) finish("cancelled");
  });
}

export interface ServerTtsOptions {
  /** 是否使用服务端 TTS（false = 跳过服务端，整轮静音） */
  enabled?: boolean;
  /** 情绪标签（英文）；一般不用设，以调用时传入的 `SpeakContext` 为准 */
  emotion?: string | null;
  /** 情绪强度 0-1（同上，仅作默认值） */
  intensity?: number;
  /** 音色 id（GPT-SoVITS 音色表 id，见 GET /media/tts/voices；空 = 后端默认音色） */
  voice?: string | null;
}

/**
 * 服务端 TTS 播报控制器（GPT-SoVITS）；拿不到音频时**保持静音**。
 *
 * 与另两个实现的关键差别：**音频与口型同源**。
 * `POST /media/avatar` 一次请求同时返回音频与 viseme 时间轴，所以：
 * - 不再需要 `useLipSyncTimeline` 另发一次请求（否则是「声音 A + 口型 B」）；
 * - 口型时钟对齐**音频真正开始播放**的时刻（渲染器以收到时间轴为 0 点）；
 * - 字幕按段推进用 `audio.currentTime` 驱动，不用定时器猜。
 *
 * 降级是**逐句**的而不是全局开关：服务在对话中途挂掉，只是后面几句没有声音，
 * 文字与字幕照常推进，不需要重启也不需要用户干预（这也是不直接抛错的原因）。
 * 刻意**不**回落到浏览器原生 TTS——那会把「角色音色」换成系统音色。
 */
export function useServerTtsAvatar(options: ServerTtsOptions = {}): AvatarController {
  const { enabled = true, emotion = null, intensity = 0.5, voice = null } = options;
  /**
   * 具身状态机由本实现自己维护。
   *
   * 早前这里把状态机委托给浏览器 TTS 实现（`...browser`）省一份代码，
   * 浏览器语音移除后就没得委托了——状态机本来就该只有一处。
   */
  const [state, setState] = useState<AvatarState>("idle");
  const [timeline, setTimeline] = useState<readonly VisemeFrame[] | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** 播报代次：与另两个实现同一思路——只有代次未变才允许继续/落地状态 */
  const generationRef = useRef(0);
  const mountedRef = useRef(false);

  /** 停掉当前音频并释放缓冲（连续对话时不释放会一直攒着已解码的数据） */
  const stopAudio = useCallback(() => {
    const audio = audioRef.current;
    audioRef.current = null;
    if (!audio) return;
    try {
      audio.pause();
    } catch {
      // 元素已不可用：忽略（停不下来不是致命错误）
    }
    audio.removeAttribute("src");
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1; // 作废进行中的播报
      abortRef.current?.abort();
      abortRef.current = null;
      stopAudio();
    };
  }, [stopAudio]);

  /**
   * 取一段音频（一次 `/media/avatar`）——**只取不播**。
   *
   * 拆成"取"与"播"两步是为了做流水线：当前段在播的时候就能去取下一段。
   * 返回 `null` = 这一段没有服务端音频（服务未部署 / 后端降级），由调用方**逐段**降级。
   */
  const fetchSegment = useCallback(
    async (
      text: string,
      context: SpeakContext | undefined,
      signal: AbortSignal,
    ): Promise<FetchedSegment | null> => {
      const data = await postAvatar(
        {
          text,
          // 本轮情绪优先；hook 上的值只是默认值
          emotion: context?.emotion ?? emotion,
          intensity: context?.intensity ?? intensity,
          voice: context?.voice ?? voice,
        },
        { signal },
      );
      if (!data?.audio_url) return null;
      return {
        audioUrl: resolveMediaUrl(data.audio_url),
        visemes: data.visemes?.length ? data.visemes : null,
        durationMs: data.duration_ms,
        textLength: text.length,
      };
    },
    [emotion, intensity, voice],
  );

  /**
   * 播一段已取回的音频并等它播完。
   *
   * 口型时间轴在 `playing`（**真正出声那一刻**）才交给渲染器：
   * 渲染器以收到时间轴的时刻为 0 点（见 live2d-renderer 的 lipSyncStartMs），
   * 提前设置会变成固定的音画偏移。
   */
  const playSegmentAudio = useCallback(
    async (
      segment: FetchedSegment,
      generation: number,
      signal: AbortSignal,
    ): Promise<SegmentPlayOutcome> => {
      const audio = new Audio(segment.audioUrl);
      audio.preload = "auto";
      audioRef.current = audio;
      const onPlaying = () => {
        if (generation === generationRef.current) setTimeline(segment.visemes);
      };
      audio.addEventListener("playing", onPlaying);

      try {
        await audio.play();
      } catch (error) {
        console.warn("[HyPRA][avatar] 服务端音频播放被浏览器拦截：", error);
        stopAudio();
        setTimeline(null);
        return "blocked";
      }

      const reason = await waitAudioFinished(
        audio,
        audioTimeoutMs(segment.durationMs, segment.textLength),
        signal,
      );
      if (reason === "timeout") {
        console.warn("[HyPRA][avatar] 服务端音频未收到结束事件，已按超时收尾");
      } else if (reason === "error") {
        // 音频元素报错（地址 404 / 解码失败）时进度事件也会停，按提前结束处理
        console.warn("[HyPRA][avatar] 服务端音频播放出错，已按提前结束处理");
      }
      stopAudio();
      if (generation === generationRef.current) setTimeline(null);
      return "played";
    },
    [stopAudio],
  );

  const speak = useCallback(
    async (text: string, _ssml?: string, context?: SpeakContext) => {
      if (!text.trim() || !enabled) return;
      const generation = ++generationRef.current;
      stopAudio();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const segment = await fetchSegment(text, context, controller.signal);
      if (generation !== generationRef.current || !mountedRef.current) return;
      if (!segment) {
        // 后端没给音频（服务未部署 / 本轮降级）→ 静音收尾，不换别的语音
        setTimeline(null);
        return;
      }
      await playSegmentAudio(segment, generation, controller.signal);
    },
    [enabled, fetchSegment, playSegmentAudio, stopAudio],
  );

  /**
   * 逐段播报（**流水线**：取一段 → 播一段 → 同时预取下一段）。
   *
   * 为什么不再"整段合成"（曾经的做法，已实测推翻）：
   * 合成耗时随文本**近似线性增长**（实测 15 字 2.99s、46 字 8.75s），
   * 整段合成意味着用户盯着文字干等 8 秒才出声。逐段后首段只需 2~3 秒，
   * 而且**字幕与该段语音同时出现**（onChunk 就在 play() 之前）。
   *
   * 预取深度刻意只留 1 段：GPU 单卡上并发请求只会排队（实测并发总时长 ≈ 串行），
   * 多排队没有收益，用户打断后还会留下一堆没用的合成。
   * 另一个刚好合适的巧合：一句的播放时长 ≈ 一句的合成耗时，所以"边播边取"天然无缝。
   */
  const speakChunks = useCallback(
    async (
      chunks: SpeechChunk[],
      onChunk?: (index: number) => void,
      context?: SpeakContext,
    ) => {
      if (chunks.length === 0 || !enabled) return;
      const generation = ++generationRef.current;
      stopAudio();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // 预取队列：始终让队列里排着接下来的 PREFETCH_DEPTH 段
      const queue: Promise<FetchedSegment | null>[] = [];
      let nextToFetch = 0;
      const enqueue = () => {
        while (nextToFetch < chunks.length && queue.length < PREFETCH_DEPTH) {
          queue.push(fetchSegment(chunks[nextToFetch].text, context, controller.signal));
          nextToFetch += 1;
        }
      };
      enqueue();

      for (let index = 0; index < chunks.length; index += 1) {
        const segment = await (queue.shift() as Promise<FetchedSegment | null>);
        if (generation !== generationRef.current || !mountedRef.current) return;
        enqueue(); // 取走一段就补上，保持领先 PREFETCH_DEPTH 段
        // 字幕与该段语音**同时**出现：这一行就在 play() 之前
        onChunk?.(index);
        if (!segment) {
          // 只有这一段拿不到音频 → 这一段静音（字幕照常推进），后面的段继续走服务端
          setTimeline(null);
          continue;
        }
        const outcome = await playSegmentAudio(segment, generation, controller.signal);
        if (outcome === "blocked") return; // 被拦：整轮静音收尾，不逐段刷屏
        if (generation !== generationRef.current) return;
      }
    },
    [enabled, fetchSegment, playSegmentAudio, stopAudio],
  );
  const interrupt = useCallback(() => {
    generationRef.current += 1; // 作废进行中的播报
    abortRef.current?.abort();
    abortRef.current = null;
    stopAudio();
    setTimeline(null);
  }, [stopAudio]);

  return {
    state,
    stateLabel: AVATAR_STATE_LABELS[state],
    /** 控制器本身可用（有没有声音由 `enabled` 与后端决定，见 detail） */
    ready: true,
    provider: "server",
    stage: enabled ? "ready" : "unconfigured",
    detail: enabled ? "" : "服务端 TTS 未启用：本轮不播报语音，只显示文字",
    setState,
    timeline,
    speak,
    speakChunks,
    interrupt,
  };
}
