"use client";

/**
 * 数字人模型库：清单、上传、删除、指定情绪映射，以及「当前用哪一个」。
 *
 * 分工：
 * - **后端**存模型文件与元数据（换浏览器不丢）；
 * - **localStorage** 只存「选了哪一个」（个人偏好）；
 * - 本 hook 是两者之间的唯一入口，避免同一个清单被多处订阅。
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  deleteAvatarModel,
  getAvatarSelection,
  listAvatarModels,
  setAvatarSelection,
  updateAvatarModel,
  uploadAvatarModel,
} from "@/lib/api/client";
import {
  getInitialSelectedModelId,
  readSelectedModelId,
  saveSelectedModelId,
  subscribeSelectedModel,
} from "@/lib/avatar/avatar-config";
import type { AvatarLayout, AvatarModelInfo, AvatarModelKind } from "@/lib/api/types";

export interface UploadModelInput {
  kind: AvatarModelKind;
  name: string;
  files: File[];
}

export interface AvatarModelsState {
  /** 后端清单（后端不可用时为空数组 → 只能使用内置模型） */
  models: AvatarModelInfo[];
  /** 是否正在拉清单（首屏 true） */
  loading: boolean;
  /** 最近一次操作的失败原因（上传/删除/更新），可直接展示给用户 */
  error: string | null;
  /** 选中的模型 id（空串 = 内置模型） */
  selectedId: string;
  /** 选中的模型详情；后端拿不到或已被删除时为 null（渲染层回落到内置模型） */
  selected: AvatarModelInfo | null;
  select: (modelId: string) => void;
  refresh: () => Promise<void>;
  upload: (input: UploadModelInput) => Promise<AvatarModelInfo>;
  remove: (modelId: string) => Promise<void>;
  /** 指定静态立绘的情绪映射（空字符串 = 该情绪不指定） */
  updateMapping: (modelId: string, expressionMap: Record<string, string>) => Promise<void>;
  /** 保存构图校准（`null` = 清除校准，回到默认构图） */
  updateLayout: (modelId: string, layout: AvatarLayout | null) => Promise<AvatarModelInfo>;
  clearError: () => void;
}

/** 把接口错误转成可读原因（后端 detail 优先） */
function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (typeof error.detail === "string" && error.detail.trim()) return error.detail;
    return `${error.message}（HTTP ${error.status}）`;
  }
  return error instanceof Error ? error.message : String(error);
}

export function useAvatarModels(): AvatarModelsState {
  const [models, setModels] = useState<AvatarModelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 首屏用内置模型（不读 localStorage），挂载后再切到真实选择
  const [selectedId, setSelectedId] = useState<string>(() => getInitialSelectedModelId());

  useEffect(() => {
    // 本地缓存先顶上：后端要一个往返，等它回来才渲染会白屏一下
    setSelectedId(readSelectedModelId());
    const unsubscribe = subscribeSelectedModel(() => setSelectedId(readSelectedModelId()));

    let cancelled = false;

    void (async () => {
      const remote = await getAvatarSelection();
      // null = 后端读不到 → 保留本地缓存（离线时仍能按上次的选择渲染）
      if (cancelled || remote === null) return;
      // 后端是唯一事实来源，本地缓存只是离线兜底：对齐一次
      saveSelectedModelId(remote);
      setSelectedId(remote);
    })();

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    const list = await listAvatarModels();
    setModels(list);
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const select = useCallback((modelId: string) => {
    // 本地立即生效：通过写入触发订阅回调，多个组件看到的是同一份状态
    saveSelectedModelId(modelId);

    // 再写后端——这是「Web 端与桌面端共用一份选择」的关键。
    // 失败**不回滚**本地值：用户明确点了这个模型，静默撤销比留下未同步更让人困惑。
    void (async () => {
      try {
        const applied = await setAvatarSelection(modelId);
        saveSelectedModelId(applied);
      } catch (caught) {
        setError(`选择未同步到后端：${describeError(caught)}`);
      }
    })();
  }, []);

  const upload = useCallback(
    async (input: UploadModelInput) => {
      setError(null);
      try {
        const model = await uploadAvatarModel(input);
        await refresh();
        return model;
      } catch (caught) {
        setError(describeError(caught));
        throw caught;
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (modelId: string) => {
      setError(null);
      try {
        await deleteAvatarModel(modelId);
        // 删掉的正是当前选中的那个 → 回到内置模型（否则渲染层会一直找不到模型）
        if (readSelectedModelId() === modelId) saveSelectedModelId("");
        await refresh();
      } catch (caught) {
        setError(describeError(caught));
        throw caught;
      }
    },
    [refresh],
  );

  const updateMapping = useCallback(
    async (modelId: string, expressionMap: Record<string, string>) => {
      setError(null);
      try {
        const updated = await updateAvatarModel(modelId, { expressionMap });
        // 就地替换：避免整表刷新导致列表闪烁
        setModels((prev) => prev.map((item) => (item.id === modelId ? updated : item)));
      } catch (caught) {
        setError(describeError(caught));
        throw caught;
      }
    },
    [],
  );

  const updateLayout = useCallback(
    async (modelId: string, layout: AvatarLayout | null) => {
      setError(null);
      try {
        const updated = await updateAvatarModel(modelId, { layout });
        // 就地替换：避免整表刷新导致列表闪烁
        setModels((prev) => prev.map((item) => (item.id === modelId ? updated : item)));
        return updated;
      } catch (caught) {
        setError(describeError(caught));
        throw caught;
      }
    },
    [],
  );

  const selected = models.find((model) => model.id === selectedId) ?? null;

  return {
    models,
    loading,
    error,
    selectedId,
    selected,
    select,
    refresh,
    upload,
    remove,
    updateMapping,
    updateLayout,
    clearError: useCallback(() => setError(null), []),
  };
}
