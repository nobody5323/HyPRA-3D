import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import {
  useServerTtsAvatar,
  useSilentAvatar,
  useXmovAvatar,
} from "@/hooks/avatar/useAvatar";
import { useAvatarCredentials } from "@/hooks/avatar/useAvatarCredentials";
import { useChatSession } from "@/hooks/chat/useChatSession";
import { usePersonaCatalog } from "@/hooks/chat/usePersonaCatalog";
import { useTtsPreferences } from "@/hooks/avatar/useTtsPreferences";
import { resolveVoiceSource } from "@/lib/avatar/avatar-config";
import { MODE_COMPANION, MODE_TAVERN } from "@/lib/chat/mode";
import { personaName } from "@/lib/chat/persona";

import { MAX_PET_SCALE, MIN_PET_SCALE, PET_SCALE_STEP } from "../../shared/ipc";
import { AvatarSurface, type AvatarRenderKind } from "./AvatarSurface";
import { resolveLocalRenderer, resolvePetRenderMode } from "./avatar-source";
import { ChatBubble } from "./ChatBubble";
import { ContextMenu, type ContextMenuItem } from "./ContextMenu";
import { resolveControlsVisible } from "./pet-chrome";
import { SettingsPanel } from "./SettingsPanel";
import { useBackendHealth } from "./useBackendHealth";
import { useDesktopBridge } from "./useDesktopBridge";
import { useDesktopPerception } from "./useDesktopPerception";
import { useWindowDrag } from "./useWindowDrag";

/** 魔珐 SDK 的挂载容器：id 给 SDK 查（它内部用选择器），常量给 hook 兑底 */
const PET_XMOV_ID = "pet-xmov-container";
const PET_XMOV_CONTAINER = `#${PET_XMOV_ID}`;

/**
 * 形象种类：本地渲染两种（Live2D / 立绘）+ 魔珐 3D。
 *
 * `.petShell[data-avatar]` 用它决定要不要画圆底座与情绪光晕——
 * 那套外观只对「没有模型时的占位形象」成立。
 */
type PetAvatarKind = AvatarRenderKind | "xmov";

function clampScale(value: number): number {
  return Math.min(MAX_PET_SCALE, Math.max(MIN_PET_SCALE, Number(value.toFixed(2))));
}

/**
 * 桌宠窗根组件。
 *
 * 分工（与 Web 端一致）：对话 / 记忆 / 情绪 / 播报指令都由后端产生，
 * 这里负责把它们接到形象渲染与桌宠的窗口交互上。
 *
 * 复用的部分：`useChatSession`（对话与记忆）、`useAvatar`（TTS 与口型）、
 * `AvatarSurface`（Live2D / 立绘）。桌宠特有的部分：拖动、穿透两态、右键菜单、
 * 设置面板、托盘联动——这些通过 `window.hyprPet` 走主进程。
 */
export function App() {
  const { bridge, state, environment } = useDesktopBridge();
  const health = useBackendHealth(true);

  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [renderStatus, setRenderStatus] = useState("");
  /** 本地渲染层报上来的种类：Live2D 真模型 vs 静态立绘（决定要不要画圆底座与光晕） */
  const [localAvatarKind, setLocalAvatarKind] = useState<AvatarRenderKind>("portrait");
  /**
   * 魔珐降级标记。
   *
   * 记下**是哪一版凭证**降级的：用户改完密钥（revision 变）就自动失效重试，
   * 否则一次失败会把 3D 永久钉死（Web 端最初就踩过这个坑）。
   */
  const [degraded, setDegraded] = useState<{ version: string; reason: string } | null>(null);
  /**
   * 指针是否在桌宠上（角色本体或底部工具条）。
   *
   * 与主进程的**穿透判定同源**：两处都在问「指针在不在可交互区域」。
   * 分开各记一份必然漂移，而漂移的表现正是最难查的那种——按钮看得见却点不动，
   * 或者鼠标已经离开了按钮还杵在那儿。
   *
   * 绑定范围只到这两个元素，不是整个窗口：窗口大半是透明的，绑窗口等于永远为真。
   */
  const [pointerInside, setPointerInside] = useState(false);
  const xmovContainerRef = useRef<HTMLDivElement | null>(null);
  /** 角色本体（`.petBody`）：既用作 xmov 挂载，也是「可交互区域」判定的锚点 */
  const bodyRef = useRef<HTMLDivElement | null>(null);
  /** 底部状态区（`.petDock`）：工具栏按钮也在可交互区域内 */
  const dockRef = useRef<HTMLDivElement | null>(null);

  // ---------------------------------------------------------------
  // 形象来源：魔珐 3D / 本地渲染（Live2D → 立绘），降级链与 Web 端同一套规则
  // ---------------------------------------------------------------
  //
  // 凭证按形态分开存：桌宠窗用**竖屏**应用（pet），Web 端用横屏（web）。
  // 控制台与桌宠窗同源（都由主进程的 loopback 服务提供），
  // 所以 localStorage 里的渲染方式偏好是共享的，而 Web 端要单独设。
  const avatarConfig = useAvatarCredentials({ form: "pet" });
  const { credentials, revision, renderer, setRenderer, save, clear, source } = avatarConfig;

  /** 凭证版本：内容或修订号变化即视为「新一版」（重新保存后允许重连） */
  const credentialsVersion = `${credentials?.appId ?? ""}:${credentials?.appSecret ?? ""}:${revision}`;
  const degradedNow = degraded?.version === credentialsVersion ? degraded : null;

  /**
   * 渲染方式决策（纯函数，见 `./avatar-source.ts`）：
   * `auto` 只在**界面填过**凭证时走魔珐，`xmov` 连部署配置的也认，
   * `local` / `static` 强制本地。
   *
   * 桌宠窗**之所以能用 3D**：魔珐 SDK 是浏览器端本地渲染且数字人自带透明通道
   * （探针实测确认），与 `transparent: true` 的透明小窗形态天然契合。
   */
  const xmovActive =
    resolvePetRenderMode({
      renderer,
      credentialsSource: source,
      degraded: Boolean(degradedNow),
    }) === "xmov";

  /**
   * 是否还在等凭证——**等的时候绝不能挂本地渲染器**。
   *
   * 首屏 `credentials` 还是 null（凭证要向后端拉），`auto` 因此会被判成「本地渲染」，
   * 于是 Live2D 立刻开始加载（注入 Cubism Core、下载模型）；等凭证到达再切到 3D 时，
   * Cubism Core 的 `<script>` 注入后**不会移除**、模型也已下载——白加载一趟。
   *
   * 用户明确选了 `local` / `static` 时不依赖凭证，无需等待。
   */
  const waitingForAvatarCredentials =
    avatarConfig.loading && renderer !== "local" && renderer !== "static";

  /**
   * 传给本地渲染层的渲染方式。
   *
   * 用户选了「魔珐」而它没起来（无凭证 / 降级）时只给**立绘**——他选的是 3D，
   * 不该自动换成另一个模型渲染器（Live2D）去跑。规则抽在纯函数里并单测钉住。
   */
  const localRenderer = resolveLocalRenderer(renderer, xmovActive);

  const xmovAvatar = useXmovAvatar(PET_XMOV_CONTAINER, {
    credentials,
    enabled: xmovActive,
    revision,
    containerRef: xmovContainerRef,
    // 建不起来 / 断线重连用尽 → 记下降级原因（换一版凭证会自动失效重试）
    onUnavailable: (reason) => setDegraded({ version: credentialsVersion, reason }),
  });

  // ---------------------------------------------------------------
  // 语音与对话（与 Web 端同一套选择规则：只有接入 TTS 才出声，否则静默）
  // ---------------------------------------------------------------
  const tts = useTtsPreferences({ enabled: health.online === true });
  const silentAvatar = useSilentAvatar();
  const serverTtsAvatar = useServerTtsAvatar({ voice: tts.voice });
  const voiceSource = resolveVoiceSource({
    // 魔珐路径只能用 SDK 自带 TTS：`speak()` 只吃 SSML，不接受外部音频
    xmovActive,
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
   * 桌面情景上报（感知层，见 `docs/proactive-multimodal.md` §4.2）。
   *
   * 它是**桌宠端独有**的能力：Web 端拿不到 SMTC / 前台窗口，因此那条路径下
   * 后端只会看到「没有桌面情景」——情景触发器静默失效，其余一切照常。
   * 上报由本层直连后端完成，主进程只负责采集（`desktop-pet.md` §4）。
   */
  const desktopPerception = useDesktopPerception();

  /**
   * 角色名（气泡、会话标签、本地渲染层的水印都用它）。
   *
   * 跟着当前人设走（`session.personaId` 来自对话偏好）；清单还没回来时
   * `personaName()` 给的是中性兜底词，**不回退「苏澄」**。
   */
  const personaCatalog = usePersonaCatalog();
  const characterLabel = personaName(personaCatalog, session.personaId);

  /** 实际渲染种类：3D 生效时直接是 xmov，否则看本地渲染层自己报的种类 */
  const avatarKind: PetAvatarKind = xmovActive ? "xmov" : localAvatarKind;

  const drag = useWindowDrag(!bridge || state.clickThrough);

  /** 气泡文本：播报中的字幕优先（随语音推进），否则显示最后一条回复 */
  const bubbleText = useMemo(() => {
    if (session.subtitle) {
      return session.subtitle;
    }

    const lastReply = [...session.messages].reverse().find((item) => item.role === "assistant");

    return lastReply?.text ?? "";
  }, [session.messages, session.subtitle]);

  const closeOverlays = useCallback(() => {
    setMenu(null);
    setSettingsOpen(false);
  }, []);

  /** 是否有浮层盖在角色之上（右键菜单 / 设置面板 / 对话面板） */
  const overlayOpen = menu !== null || settingsOpen || chatOpen;

  /** 底部工具条是否显示（指针在桌宠上，或用户正在用某个浮层） */
  const controlsVisible = resolveControlsVisible({ pointerInside, overlayOpen });

  /**
   * 对话面板打开时通知主进程**加宽窗口**（面板占新多出来的右侧一栏）。
   *
   * 不在原窗口里挤一块：那样会把模型压小、或把它推到一边，看起来就是
   * 「模型被压缩了」。加宽之后模型区仍是原来的宽度（CSS 把它锁在左侧），
   * 模型的位置与大小都不变。
   */
  useEffect(() => {
    void bridge?.setChatPanel(chatOpen);
  }, [bridge, chatOpen]);

  /**
   * 按鼠标当前位置同步主进程的**穿透**状态。
   *
   * 用 `:hover` 而不是自己记一个状态：鼠标在角色与工具栏之间移动会先 `leave` 再
   * `enter`，自己记就会在中间那一帧误判成「离开了」，把点击吃掉。
   * 延迟一帧执行，确保 CSS 的 hover 已更新。
   */
  const syncInteractiveFromPointer = useCallback(() => {
    if (!bridge) {
      return;
    }

    window.requestAnimationFrame(() => {
      const hovering = [bodyRef.current, dockRef.current].some(
        (element) => element?.matches(":hover") ?? false,
      );

      // 顺手把控件显隐也对齐到真实指针位置：`pointerleave` 与 `pointerenter`（角色 → 工具条）
      // 之间会有一瞬「两边都不算」，只靠事件会让工具条闪一下。
      setPointerInside(hovering);
      void bridge.setControlInteractive(hovering);
    });
  }, [bridge]);

  /** 指针进入可交互区域：立即恢复点击（不等下一帧，否则第一下点不到） */
  const handleInteractiveEnter = useCallback(() => {
    setPointerInside(true);
    void bridge?.setControlInteractive(true);
  }, [bridge]);

  /**
   * 指针离开可交互区域。
   *
   * 浮层打开期间**不收回**交互权：鼠标从角色移到菜单 / 面板上必然触发 `leave`
   * （浮层画在角色区域之外），此刻若开启穿透，浮层立刻点不动——
   * 这正是「右键弹出菜单后点什么都没用」的根因（只在穿透模式下复现）。
   * 浮层关闭时再由下面的 effect 按鼠标位置恢复。
   */
  const handleInteractiveLeave = useCallback(() => {
    if (overlayOpen) {
      return;
    }

    setPointerInside(false);
    syncInteractiveFromPointer();
  }, [overlayOpen, syncInteractiveFromPointer]);

  /**
   * 浮层开关时同步穿透状态：
   * **打开**则强制可交互（菜单 / 面板必须能点）；
   * **关闭**则回到「鼠标在哪就在哪」的真实状态，免得鼠标已离开角色、窗口却仍然接收点击。
   */
  useEffect(() => {
    if (!bridge) {
      return;
    }

    if (overlayOpen) {
      void bridge.setControlInteractive(true);

      return;
    }

    const frame = window.requestAnimationFrame(() => {
      const hovering = [bodyRef.current, dockRef.current].some(
        (element) => element?.matches(":hover") ?? false,
      );

      // 浮层期间 `leave` 被忽略，指针位置可能已经跟真实位置脱节：关掉时一并纠回来
      setPointerInside(hovering);
      void bridge.setControlInteractive(hovering);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [bridge, overlayOpen]);

  // Esc 关闭浮层（聊天面板单独处理，它是常驻可交互区域）
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        closeOverlays();
      }
    };

    window.addEventListener("keydown", onKeyDown);

    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeOverlays]);

  /**
   * 交互模式的两项。
   *
   * 显示的是「当前选定值，没选过则显示后端实际生效的那个」——两者不一致时以实际
   * 生效的为准，否则用户会看到「一个都没勾」，而对话明明按酒馆模式在跑
   * （没选过时后端会按酒馆预设推断，见 `app/session/mode.py`）。
   */
  const currentMode = session.mode || session.effectiveMode;

  const menuItems: ContextMenuItem[] = [
    {
      key: "chat",
      label: chatOpen ? "收起对话" : "打开对话",
      onSelect: () => setChatOpen((open) => !open),
    },
    { key: "settings", label: "桌宠设置", onSelect: () => setSettingsOpen(true) },
    {
      key: "mode-companion",
      label: "桌宠对话模式",
      hint: "酒馆世界书不参与召回",
      role: "menuitemradio",
      checked: currentMode === MODE_COMPANION,
      separatorBefore: true,
      onSelect: () => session.setMode(MODE_COMPANION),
    },
    {
      key: "mode-tavern",
      label: "酒馆聊天模式",
      hint: "酒馆世界书参与召回",
      role: "menuitemradio",
      checked: currentMode === MODE_TAVERN,
      onSelect: () => session.setMode(MODE_TAVERN),
    },
    {
      key: "clickThrough",
      label: "点击穿透",
      checked: state.clickThrough,
      separatorBefore: true,
      onSelect: () => void bridge?.setClickThrough(!state.clickThrough),
    },
    {
      key: "alwaysOnTop",
      label: "窗口置顶",
      checked: state.alwaysOnTop,
      onSelect: () => void bridge?.updateSettings({ alwaysOnTop: !state.alwaysOnTop }),
    },
    {
      key: "zoomIn",
      label: "放大",
      separatorBefore: true,
      disabled: state.scale >= MAX_PET_SCALE,
      onSelect: () => void bridge?.updateSettings({ scale: clampScale(state.scale + PET_SCALE_STEP) }),
    },
    {
      key: "zoomOut",
      label: "缩小",
      disabled: state.scale <= MIN_PET_SCALE,
      onSelect: () => void bridge?.updateSettings({ scale: clampScale(state.scale - PET_SCALE_STEP) }),
    },
    { key: "hide", label: "隐藏桌宠", separatorBefore: true, onSelect: () => bridge?.hide() },
    { key: "quit", label: "退出 HyPRA", onSelect: () => bridge?.quit() },
  ];

  return (
    <div
      className="petShell"
      style={{ "--pet-scale": state.scale } as CSSProperties}
      data-click-through={state.clickThrough}
      data-avatar={avatarKind}
      /*
        底部工具条的显隐：指针不在桌宠上就隐回去（规则与理由见 `pet-chrome.ts`）。
        不用 CSS 的 `:hover` 单独搞定：显隐必须与主进程的穿透判定同源，
        否则会出现「按钮看得见但点不动」（窗口还处于穿透态）。
      */
      data-controls={controlsVisible ? "show" : "hide"}
      /* 对话面板打开时角色让位（CSS 按这个属性腾出右侧空间，避免面板压住模型） */
      data-chat-open={chatOpen}
    >
      {/* 拖动热区：整窗口可用（气泡与菜单在它之上，且各自处理自己的指针事件） */}
      <div
        className={`petStage${drag.dragging ? " petStageDragging" : ""}`}
        onPointerDown={drag.onPointerDown}
        onPointerMove={drag.onPointerMove}
        onPointerUp={drag.onPointerUp}
        onPointerCancel={drag.onPointerCancel}
        onContextMenu={(event) => {
          event.preventDefault();
          closeOverlays();
          setMenu({ x: event.clientX, y: event.clientY });
        }}
      >
        {bubbleText ? (
          <p className="petBubble" role="status" aria-live="polite">
            {bubbleText}
          </p>
        ) : null}

        <div className="petGlow" aria-hidden="true" />

        {/*
          指针进出只绑在角色本体上（不是整个窗口）：
          窗口是 280×480 的矩形，包含大片透明区域，
          绑在窗口上会让「鼠标进入角色区域」永远为真，解锁入口就常显了。
        */}
        <div
          ref={bodyRef}
          className="petBody"
          onPointerEnter={handleInteractiveEnter}
          onPointerLeave={handleInteractiveLeave}
        >
          {/*
            魔珐 3D 容器：**始终挂载**（SDK 在 init 时读容器宽高，尺寸为 0 会失败）。
            未激活时它只是个空盒子，由下面的本地渲染层叠在上面显示。
            容器是矩形而数字人四周透明，所以指针事件全部交给 `.petBody`
            （CSS 里 `.petXmov` 的 pointer-events: none）。
          */}
          <div className="petXmov" aria-hidden={!xmovActive}>
            <div
              id={PET_XMOV_ID}
              ref={xmovContainerRef}
              className="petXmovStage"
              role={xmovActive ? "img" : undefined}
              aria-label={xmovActive ? "数字人舞台：魔珐星云实时渲染画面" : undefined}
            />
          </div>

          {/*
            本地渲染层（Live2D / 立绘）。
            两套渲染同时跑白耗 GPU 且互相遮挡，所以魔珐生效时不挂；
            凭证还没拉到时也不挂——否则会白加载一遍 Live2D（见 waitingForAvatarCredentials）。
          */}
          {waitingForAvatarCredentials || xmovActive ? null : (
            <AvatarSurface
              emotion={session.emotion}
              motion={avatar.state}
              lipSync={avatar.timeline ?? null}
              characterLabel={characterLabel}
              renderer={localRenderer}
              onStatusChange={setRenderStatus}
              onAvailabilityChange={setLocalAvatarKind}
            />
          )}

          {state.clickThrough && state.controlInteractive ? (
            <button
              type="button"
              className="petUnlock"
              title="恢复交互（可点、可拖、可聊天）"
              onClick={() => void bridge?.setClickThrough(false)}
            >
              解锁交互
            </button>
          ) : null}
        </div>
      </div>

      {/*
        底部状态区：未连接后端 / 模型加载中 / 情绪。

        「模型加载中」只在**真的在加载**时才有：加载完（含立绘就位）后
        `AvatarSurface` 会上报空串，这里就不渲染——常挂一行「正在加载模型…」
        在模型早就画好之后只是噪音（规则与单测见 `pet-chrome.ts`）。

        指针进出也参与穿透判定：只绑角色本体的话，底部工具栏（聊天 / 穿透 / 隐藏）
        在穿透模式下永远点不动——鼠标从角色移到工具栏会先触发角色的 leave。
      */}
      <div
        ref={dockRef}
        className="petDock"
        onPointerEnter={handleInteractiveEnter}
        onPointerLeave={handleInteractiveLeave}
      >
        {!bridge ? (
          <span className="petDockBadge">浏览器调试模式：窗口控制不可用</span>
        ) : null}
        {health.online === false ? (
          <span className="petDockBadge petDockBadgeWarn">后端未连接</span>
        ) : null}
        {renderStatus ? <span className="petDockBadge">{renderStatus}</span> : null}
        {session.emotion ? (
          <span className="petDockBadge petDockBadgeMood" data-emotion={session.emotion.label}>
            {session.emotion.label_zh}
          </span>
        ) : null}

        <div className="petToolbar">
          <button type="button" onClick={() => setChatOpen((open) => !open)}>
            {chatOpen ? "收起" : "聊天"}
          </button>
          <button type="button" onClick={() => void bridge?.setClickThrough(!state.clickThrough)}>
            穿透
          </button>
          <button type="button" onClick={() => bridge?.hide()} title="隐藏到托盘（Ctrl+Alt+P 唤出）">
            隐藏
          </button>
        </div>
      </div>

      {chatOpen ? (
        <ChatBubble
          session={session}
          characterLabel={characterLabel}
          onClose={() => setChatOpen(false)}
        />
      ) : null}

      {settingsOpen ? (
        /*
          只挑桌宠窗真正要用的会话能力，不透传整个 `session`：那个对象有二十多个
          字段（播报 / 记忆统计 / 流式开关…），透传进来会让这个文件的依赖面变成
          「所有东西」（与 `avatar` 同一理由）。
        */
        <SettingsPanel
          perception={desktopPerception}
          state={state}
          environment={environment}
          health={health}
          userName={session.userName}
          onSaveUserName={session.setUserName}
          chat={{
            personaId: session.personaId,
            sessionId: session.sessionId,
            version: session.sessionVersion,
            catalog: personaCatalog,
            selectPersona: session.setPersonaId,
            openSession: session.openSession,
            newSession: session.newSession,
            removeSession: session.removeSession,
            clearAll: session.clearAllSessions,
            busy: session.busy,
          }}
          /*
            预设四项（文风 / 提示词 / 酒馆 / 叙事框架）走**会话级**选择：改了立刻
            作用于下一轮，同时写回后端共享偏好（见 useChatSession 的 changeStyle /
            changePreset / changeJailbreak）。
            清单由 PresetPanel 自己在挂载时拉——它只在设置面板打开时挂载。
          */
          presets={{
            styleId: session.styleId,
            selectStyle: session.setStyleId,
            presetId: session.presetId,
            selectPreset: session.setPresetId,
            stPresetId: session.stPresetId,
            selectStPreset: session.setStPresetId,
            jailbreakId: session.jailbreakId,
            selectJailbreak: session.setJailbreakId,
            busy: session.busy,
          }}
          avatar={{
            renderer,
            onRendererChange: setRenderer,
            credentials,
            source,
            save,
            clear,
            degradedReason: degradedNow?.reason ?? null,
            xmovActive,
            loading: avatarConfig.loading,
          }}
          onUpdateScale={(scale) => void bridge?.updateSettings({ scale: clampScale(scale) })}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}

      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} items={menuItems} onClose={() => setMenu(null)} />
      ) : null}
    </div>
  );
}
