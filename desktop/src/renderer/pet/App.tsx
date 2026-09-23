import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";

import { useBrowserAvatar, useServerTtsAvatar } from "@/hooks/avatar/useAvatar";
import { useChatSession } from "@/hooks/chat/useChatSession";
import { useTtsPreferences } from "@/hooks/avatar/useTtsPreferences";
import { resolveVoiceSource } from "@/lib/avatar/avatar-config";

import { MAX_PET_SCALE, MIN_PET_SCALE, PET_SCALE_STEP } from "../../shared/ipc";
import { AvatarSurface } from "./AvatarSurface";
import { ChatBubble } from "./ChatBubble";
import { ContextMenu, type ContextMenuItem } from "./ContextMenu";
import { SettingsPanel } from "./SettingsPanel";
import { useBackendHealth } from "./useBackendHealth";
import { useDesktopBridge } from "./useDesktopBridge";
import { useWindowDrag } from "./useWindowDrag";

/** 角色名与用户称呼：与后端人设默认值保持一致（Web 端同样是这两个） */
const CHARACTER_LABEL = "苏澄";
const USER_NAME = "小林";

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

  // ---------------------------------------------------------------
  // 语音与对话（与 Web 端同一套选择规则：服务端 TTS 不可用则回落浏览器语音）
  // ---------------------------------------------------------------
  const tts = useTtsPreferences({ enabled: health.online === true });
  const browserAvatar = useBrowserAvatar();
  const serverTtsAvatar = useServerTtsAvatar({ voice: tts.voice });
  const voiceSource = resolveVoiceSource({
    xmovActive: false, // 桌面端不做魔珐 3D（那是 Web 端的形态），只走 Live2D / 立绘
    serverTtsAvailable: tts.serverTtsAvailable,
    preference: tts.engine,
  });
  const avatar = voiceSource === "server" ? serverTtsAvatar : browserAvatar;
  const session = useChatSession(avatar, USER_NAME);

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

  const menuItems: ContextMenuItem[] = [
    {
      key: "chat",
      label: chatOpen ? "收起对话" : "打开对话",
      onSelect: () => setChatOpen((open) => !open),
    },
    { key: "settings", label: "桌宠设置", onSelect: () => setSettingsOpen(true) },
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
          窗口是 380×480 的矩形，包含大片透明区域，
          绑在窗口上会让「鼠标进入角色区域」永远为真，解锁入口就常显了。
        */}
        <div
          className="petBody"
          onPointerEnter={() => void bridge?.setControlInteractive(true)}
          onPointerLeave={() => void bridge?.setControlInteractive(false)}
        >
          <AvatarSurface
            emotion={session.emotion}
            motion={avatar.state}
            lipSync={avatar.timeline ?? null}
            characterLabel={CHARACTER_LABEL}
            onStatusChange={setRenderStatus}
          />

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

      {/* 底部状态区：未连接后端 / 模型加载中 / 情绪 */}
      <div className="petDock">
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
          characterLabel={CHARACTER_LABEL}
          onClose={() => setChatOpen(false)}
        />
      ) : null}

      {settingsOpen ? (
        <SettingsPanel
          state={state}
          environment={environment}
          health={health}
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
