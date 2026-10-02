import { useCallback, useEffect, useState } from "react";

import {
  clearActivity,
  getActivity,
  getPerceptionStatus,
  reportDesktopContext,
  type ActivityReport,
} from "@/lib/api/client";

/**
 * 桌面情景上报：主进程采集 → 渲染层直连后端。
 *
 * 设计见 `docs/proactive-multimodal.md` §4.2。三条边界：
 *
 * 1. **主进程只采集、不联网**（`desktop-pet.md` §4 的既有约定）；
 *    上报由渲染层直连后端完成——`window.hyprPet.getDesktopContext()` 只回数据。
 * 2. **后端开关是唯一裁决者**。启动时先问一次 `/perception/status`：
 *    `desktop_enabled=false` 就**根本不上报**（而不是上报了让后端丢掉）。
 *    上报响应里若回 `accepted:false`（用户中途关掉了），立刻停止轮询——
 *    §4.6 的红线是「关掉开关后采集链路立即停止」。
 * 3. **任何失败都静默降级**。拿不到桌面情景只是少了主动开口的由头，
 *    不该让桌宠窗显示错误、更不该让对话不可用。
 */

/** 上报间隔（毫秒）。必须大于主进程的采集缓存时长（10s），否则每次都要起 PowerShell */
const REPORT_INTERVAL_MS = 30_000;

/** 首次上报的延迟：等界面先把首屏渲染出来，别和模型加载抢资源 */
const FIRST_REPORT_DELAY_MS = 5_000;

/**
 * 行踪的刷新间隔（毫秒）。
 *
 * 比上报间隔长：行踪面板是给人看的，一分钟更新一次足够，
 * 而**每次刷新都要读后端的日文件**——按上报频率去拉没有意义。
 */
const ACTIVITY_INTERVAL_MS = 60_000;

/** 电池信息（`navigator.getBattery()` 不在标准 lib.d.ts 里，这里补最小类型） */
interface BatteryManagerLike extends EventTarget {
  level: number;
  charging: boolean;
}

interface NavigatorWithBattery extends Navigator {
  getBattery?: () => Promise<BatteryManagerLike>;
}

export interface DesktopPerception {
  /** 后端是否接受桌面情景（开关 + 是否已成功上报过） */
  enabled: boolean;
  /** 最近一次上报的感知文本（后端渲染好的「此刻」行；空 = 没拿到） */
  lastPerceptionText: string;
  /** 最近一次失败原因（仅用于设置面板展示，不阻断任何功能） */
  error: string | null;
  /**
   * 行踪与偏好画像（`docs/proactive-multimodal.md` §4.9）。
   *
   * **必须可见**：行踪是「历史」，比「此刻」敏感得多。用户在设置里
   * 要能一眼看到「它到底记了什么」，否则只能靠它说的话去猜——
   * 而猜出来的结论一定比实际更糟。
   */
  activity: ActivityReport | null;
  /** 清空全部行踪记录（一次清干净，不是只清今天的） */
  clearActivity(): Promise<string>;
}

export function useDesktopPerception(): DesktopPerception {
  const [enabled, setEnabled] = useState(false);
  const [lastPerceptionText, setLastPerceptionText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [activity, setActivity] = useState<ActivityReport | null>(null);

  const refreshActivity = useCallback(async () => {
    // 行踪读不到只是少了展示，不该让设置面板显示错误
    setActivity(await getActivity({ hours: 12 }));
  }, []);

  const clear = useCallback(async (): Promise<string> => {
    try {
      const result = await clearActivity();
      await refreshActivity();
      return result.message || "已清除行踪记录。";
    } catch (err) {
      return err instanceof Error ? `清除失败：${err.message}` : "清除失败：后端不可达。";
    }
  }, [refreshActivity]);

  useEffect(() => {
    const bridge = window.hyprPet;
    if (!bridge) {
      // 浏览器里直接打开本页面调试：没有桌面能力，静默不启用
      return;
    }

    let active = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    let activityTimer: ReturnType<typeof setInterval> | null = null;

    const readBattery = async (): Promise<{
      battery_percent: number | null;
      battery_charging: boolean | null;
    }> => {
      const getBattery = (navigator as NavigatorWithBattery).getBattery;
      if (typeof getBattery !== "function") {
        // 台式机 / 权限受限：回 null 表示「拿不到」，
        // 而不是回 0%（那会被后端读成「电量耗尽」并触发一次没必要的关心）
        return { battery_percent: null, battery_charging: null };
      }
      try {
        const battery = await getBattery.call(navigator);
        return {
          battery_percent: Math.round(battery.level * 100),
          battery_charging: battery.charging,
        };
      } catch {
        return { battery_percent: null, battery_charging: null };
      }
    };

    const report = async (): Promise<boolean> => {
      try {
        const snapshot = await bridge.getDesktopContext();
        const battery = await readBattery();
        const result = await reportDesktopContext({ ...snapshot, ...battery });
        if (!active) return false;
        setLastPerceptionText(result.perception_text);
        setError(null);
        if (!result.accepted) {
          // 后端开关关掉了 → 立刻停止，且不再继续轮询
          setEnabled(false);
          return false;
        }
        setEnabled(true);
        return true;
      } catch (err) {
        if (!active) return false;
        // 后端没起 / 网络抖动：只记原因，下个周期再试
        setError(err instanceof Error ? err.message : "桌面情景上报失败");
        return true;
      }
    };

    const start = async () => {
      const status = await getPerceptionStatus();
      if (!active) return;
      if (!status?.desktop_enabled) {
        // 后端开关关着：**根本不上报**（不是上报了让后端丢掉）
        setEnabled(false);
        return;
      }
      const keepGoing = await report();
      if (!active || !keepGoing) return;
      // 行踪面板：首次立刻拉一次，之后低频刷新
      void refreshActivity();
      activityTimer = setInterval(() => void refreshActivity(), ACTIVITY_INTERVAL_MS);
      timer = setInterval(() => {
        void report().then((continues) => {
          if (!continues && timer) {
            clearInterval(timer);
            timer = null;
          }
        });
      }, REPORT_INTERVAL_MS);
    };

    const firstTimer = setTimeout(() => void start(), FIRST_REPORT_DELAY_MS);

    return () => {
      active = false;
      clearTimeout(firstTimer);
      if (timer) clearInterval(timer);
      if (activityTimer) clearInterval(activityTimer);
    };
  }, [refreshActivity]);

  return {
    enabled,
    lastPerceptionText,
    error,
    activity,
    clearActivity: clear,
  };
}
