"use client";

/**
 * 订阅数字人凭证与渲染偏好的 React Hook。
 *
 * 凭证**来自后端**（`GET|PUT /media/avatar/credentials`，按形态区分）：三个界面的
 * origin 不同（控制台 / 桌宠窗是 `127.0.0.1:34567`，Web 端是 `localhost:3000`），
 * localStorage 天然不共享，而「用哪套密钥」必须是三处一致的事实。
 *
 * 首屏**不**请求后端（SSR 也拿不到结果，两边渲染必须一致），挂载后再拉。
 * 顺带负责两件事：
 * - 把**旧版**存在 localStorage 里的凭证一次性搬到后端（搬完清掉旧键）；
 * - 渲染方式偏好（纯前端、各 origin 各存各的）的读写信道。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type AvatarCredentials,
  type AvatarRendererPreference,
  type CredentialSource,
  clearLegacyStoredCredentials,
  getInitialRendererPreference,
  readLegacyStoredCredentials,
  readRendererPreference,
  saveRendererPreference,
  subscribeRendererPreference,
} from "@/lib/avatar/avatar-config";
import { getAvatarCredentials, putAvatarCredentials } from "@/lib/api/client";
import type { AvatarCredentialForm, AvatarCredentialsResponse } from "@/lib/api/types";

export interface AvatarCredentialsState {
  credentials: AvatarCredentials | null;
  source: CredentialSource;
  configured: boolean;
  /** 首屏向后端拉取中（拉不到时为 false，此时 source 保持 none） */
  loading: boolean;
  /** 配置修订号：每次保存 / 清除 +1（即使内容相同），用于触发重新连接 */
  revision: number;
  /** 当前订阅的凭证形态（`web` = 横屏给 Web 端，`pet` = 竖屏给桌宠窗） */
  form: AvatarCredentialForm;
  /** 保存到后端；返回 false = 后端不可达或被拒（由调用方给出提示） */
  save: (credentials: AvatarCredentials) => Promise<boolean>;
  /** 清除后端保存的该形态凭证（回落到部署配置） */
  clear: () => Promise<boolean>;
  /**
   * 渲染方式偏好（自动 / 本地渲染器 / 魔珐 / 仅静态立绘）。
   *
   * 与凭证放在同一个 hook 里：两者都是「数字人配置」，
   * 合并订阅可以避免同一个 localStorage 被多处监听。
   */
  renderer: AvatarRendererPreference;
  setRenderer: (value: AvatarRendererPreference) => void;
}

/** 界面快照：`credentials` 为空时 `source` 只用于文案展示 */
interface CredentialsSnapshot {
  credentials: AvatarCredentials | null;
  source: CredentialSource;
}

const UNCONFIGURED: CredentialsSnapshot = { credentials: null, source: "none" };

/** 后端响应 → 界面快照（两边字段名不同，集中在这里翻译一次） */
function toSnapshot(response: AvatarCredentialsResponse): CredentialsSnapshot {
  return {
    credentials: response.configured
      ? { appId: response.appId, appSecret: response.appSecret }
      : null,
    source: response.source,
  };
}

export function useAvatarCredentials(
  options: { form?: AvatarCredentialForm } = {},
): AvatarCredentialsState {
  const form = options.form ?? "web";
  // 首屏用「未配置」的安全值（SSR 拿不到后端结果，客户端首屏也必须一致）
  const [snapshot, setSnapshot] = useState<CredentialsSnapshot>(UNCONFIGURED);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [renderer, setRendererState] = useState<AvatarRendererPreference>(() =>
    getInitialRendererPreference(),
  );

  // 渲染方式偏好：挂载后读 localStorage，并跟随变化（纯前端，与凭证无关）
  useEffect(() => {
    setRendererState(readRendererPreference());
    return subscribeRendererPreference(() => {
      setRendererState(readRendererPreference());
    });
  }, []);

  // 凭证：挂载后拉后端；后端说「没配」时，把旧版 localStorage 里的搬过去
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      setLoading(true);
      const remote = await getAvatarCredentials(form);
      if (cancelled) return;

      if (remote && remote.source === "none") {
        const legacy = readLegacyStoredCredentials();
        if (legacy) {
          const migrated = await putAvatarCredentials({ form, ...legacy });
          if (cancelled) return;
          if (migrated) {
            clearLegacyStoredCredentials();
            setSnapshot(toSnapshot(migrated));
            // 首屏就从「未配置」变成「已配置」：必须走 revision，
            // 否则上层的主体逻辑不会重建魔珐会话
            setRevision((prev) => prev + 1);
            setLoading(false);
            return;
          }
        }
      }

      // `remote` 为 null = 后端读不到（离线 / 5xx），与「后端说没配」是两件事：
      // 这时保持现状，不能因为一次离线请求就把用户看着的渲染器换掉
      if (remote) setSnapshot(toSnapshot(remote));
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [form]);

  /** 写接口的成功结果统一走这里：更新快照并推进 revision（触发重连） */
  const applyResponse = useCallback((response: AvatarCredentialsResponse) => {
    setSnapshot(toSnapshot(response));
    setRevision((prev) => prev + 1);
  }, []);

  const save = useCallback(
    async (credentials: AvatarCredentials): Promise<boolean> => {
      const saved = await putAvatarCredentials({
        form,
        appId: credentials.appId.trim(),
        appSecret: credentials.appSecret.trim(),
      });
      if (!saved) return false;
      applyResponse(saved);
      return true;
    },
    [form, applyResponse],
  );

  const clear = useCallback(async (): Promise<boolean> => {
    const cleared = await putAvatarCredentials({ form, appId: "", appSecret: "" });
    if (!cleared) return false;
    applyResponse(cleared);
    return true;
  }, [form, applyResponse]);

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
    loading,
    revision,
    form,
    save,
    clear,
    renderer,
    setRenderer: saveRendererPreference,
  };
}
