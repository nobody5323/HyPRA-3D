import { useEffect, useState } from "react";

import {
  DEFAULT_PET_SETTINGS,
  type PetEnvironment,
  type PetSettings,
  type PetWindowState,
} from "../../shared/ipc";

/**
 * 桌面能力状态。
 *
 * 全部懒加载 + 订阅：渲染层不假设 IPC 一定存在（浏览器里直接打开本页面调试时
 * `window.hyprPet` 就是 undefined），这种情况下界面仍要能渲染出来。
 */
export interface DesktopBridge {
  /** preload 注入的能力；浏览器调试时为 undefined */
  bridge: typeof window.hyprPet;
  state: PetWindowState;
  settings: PetSettings;
  environment: PetEnvironment | null;
}

/** 首帧用的中性状态（与主进程初始值一致，避免界面先抖一下） */
const INITIAL_STATE: PetWindowState = {
  visible: true,
  clickThrough: false,
  controlInteractive: false,
  scale: 1,
  alwaysOnTop: true,
};

export function useDesktopBridge(): DesktopBridge {
  const bridge = window.hyprPet;
  const [state, setState] = useState<PetWindowState>(INITIAL_STATE);
  const [settings, setSettings] = useState<PetSettings>(DEFAULT_PET_SETTINGS);
  const [environment, setEnvironment] = useState<PetEnvironment | null>(null);

  useEffect(() => {
    if (!bridge) {
      return;
    }

    let active = true;

    void bridge.getState().then((next) => {
      if (active) {
        setState(next);
      }
    });
    void bridge.getSettings().then((next) => {
      if (active) {
        setSettings(next);
      }
    });
    void bridge.getEnvironment().then((next) => {
      if (active) {
        setEnvironment(next);
      }
    });

    const unsubscribeState = bridge.onStateChanged(setState);
    const unsubscribeSettings = bridge.onSettingsChanged(setSettings);

    return () => {
      active = false;
      unsubscribeState();
      unsubscribeSettings();
    };
  }, [bridge]);

  return { bridge, state, settings, environment };
}
