import { useEffect, useState } from "react";

import {
  DEFAULT_CONSOLE_SETTINGS,
  type ConsoleSettings,
  type ConsoleState,
} from "../../shared/ipc";

/**
 * 控制台窗的桌面能力状态。
 *
 * 与桌宠窗的 `useDesktopBridge` 同一套做法：懒加载 + 订阅，且**不假设 IPC 一定存在**
 * （浏览器里直接打开本页面调试时 `window.hyprConsole` 就是 undefined）。
 */
export interface ConsoleBridgeState {
  /** preload 注入的能力；浏览器调试时为 undefined */
  bridge: typeof window.hyprConsole;
  /** 主进程状态快照（首帧为 null，避免拿默认值骗界面） */
  state: ConsoleState | null;
  settings: ConsoleSettings;
}

export function useConsoleBridge(): ConsoleBridgeState {
  const bridge = window.hyprConsole;
  const [state, setState] = useState<ConsoleState | null>(null);
  const [settings, setSettings] = useState<ConsoleSettings>(DEFAULT_CONSOLE_SETTINGS);

  useEffect(() => {
    if (!bridge) {
      return;
    }

    let active = true;

    void bridge.getState().then((next) => {
      if (active) {
        setState(next);
        setSettings(next.settings);
      }
    });

    const unsubscribeState = bridge.onStateChanged((next) => {
      setState(next);
      setSettings(next.settings);
    });
    const unsubscribeSettings = bridge.onSettingsChanged(setSettings);

    return () => {
      active = false;
      unsubscribeState();
      unsubscribeSettings();
    };
  }, [bridge]);

  return { bridge, state, settings };
}
