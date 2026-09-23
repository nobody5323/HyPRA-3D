import { useEffect, useState } from "react";

import { getHealth } from "@/lib/api/client";

/** 后端在线探测的间隔（毫秒）：桌面端常驻，没必要频繁打 */
const POLL_INTERVAL_MS = 15_000;

export interface BackendHealth {
  /** `null` = 尚未探测出结果（首屏） */
  online: boolean | null;
  /** 后端版本信息（在线时） */
  appName: string | null;
}

/**
 * 后端健康检查。
 *
 * 桌宠窗是常驻的，后端却是另一个进程（docker compose / uvicorn），
 * 因此必须能区分「后端没起来」和「程序坏了」——前者给提示，后者才该报错。
 */
export function useBackendHealth(enabled: boolean): BackendHealth {
  const [online, setOnline] = useState<boolean | null>(null);
  const [appName, setAppName] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let active = true;

    const probe = async (): Promise<void> => {
      // getHealth 内部已把网络错误吞掉（返回 null），这里只需区分 null 与非 null
      const status = await getHealth();

      if (!active) {
        return;
      }

      setOnline(status !== null);
      setAppName(status?.app ?? null);
    };

    void probe();
    const timer = window.setInterval(() => void probe(), POLL_INTERVAL_MS);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [enabled]);

  return { online, appName };
}
