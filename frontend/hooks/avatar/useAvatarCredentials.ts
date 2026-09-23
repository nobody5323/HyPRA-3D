"use client";

/**
 * 订阅数字人凭证配置的 React Hook。
 *
 * 首屏使用构建时兜底值（避免 SSR/CSR 不一致导致的 hydration 警告），
 * 挂载后再读取 localStorage 并在凭证变化时同步更新。
 */

import { useEffect, useMemo, useState } from "react";

import {
  type AvatarCredentials,
  type AvatarRendererPreference,
  type CredentialSource,
  clearCredentials,
  getEffectiveCredentials,
  getInitialCredentials,
  getInitialRendererPreference,
  readRendererPreference,
  saveCredentials,
  saveRendererPreference,
  subscribeCredentials,
  subscribeRendererPreference,
} from "@/lib/avatar/avatar-config";

export interface AvatarCredentialsState {
  credentials: AvatarCredentials | null;
  source: CredentialSource;
  configured: boolean;
  /** 配置修订号：每次保存/清除 +1（即使内容相同），用于触发重新连接 */
  revision: number;
  save: (credentials: AvatarCredentials) => boolean;
  clear: () => void;
  /**
   * 渲染方式偏好（自动 / 本地渲染器 / 魔珐 / 仅静态立绘）。
   *
   * 与凭证放在同一个 hook 里：两者都是「数字人配置」，
   * 合并订阅可以避免同一个 localStorage 被多处监听。
   */
  renderer: AvatarRendererPreference;
  setRenderer: (value: AvatarRendererPreference) => void;
}

export function useAvatarCredentials(): AvatarCredentialsState {
  // 首屏用「不读 localStorage」的安全值（避免 hydration 不一致），挂载后再读真实配置
  const [snapshot, setSnapshot] = useState(() => getInitialCredentials());
  const [revision, setRevision] = useState(0);
  const [renderer, setRendererState] = useState<AvatarRendererPreference>(() =>
    getInitialRendererPreference(),
  );

  useEffect(() => {
    setSnapshot(getEffectiveCredentials()); // 挂载后读取 localStorage
    setRendererState(readRendererPreference());
    const unsubscribeCredentials = subscribeCredentials(() => {
      setSnapshot(getEffectiveCredentials());
      setRevision((prev) => prev + 1); // 即使内容不变也触发重连（如点「重新连接」）
    });
    const unsubscribeRenderer = subscribeRendererPreference(() => {
      setRendererState(readRendererPreference());
    });
    return () => {
      unsubscribeCredentials();
      unsubscribeRenderer();
    };
  }, []);

  // 稳定引用：仅在凭证内容变化时更新（便于作为 effect 依赖）
  const credentials = useMemo(
    () => snapshot.credentials,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [snapshot.credentials?.appId, snapshot.credentials?.appSecret],
  );

  return {
    credentials,
    source: snapshot.source,
    configured: Boolean(credentials),
    revision,
    save: saveCredentials,
    clear: clearCredentials,
    renderer,
    setRenderer: saveRendererPreference,
  };
}
