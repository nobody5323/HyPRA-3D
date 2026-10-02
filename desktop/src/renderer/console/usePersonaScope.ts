/**
 * 控制台里「当前陪伴对象」的唯一来源。
 *
 * 为什么必须只有一个来源：陪伴对象 id 同时是**记忆隔离命名空间**
 * （`AGENTS.md §8.2` 的 `companion:{id}`）。控制台里至少三处要用它——
 * 人设与文风、会话与记忆、能力中心里的「酒馆记忆导入」——
 * 各算各的就会出现「这一页说苏澄、那一页说陈小满」，而用户完全无从判断
 * 自己点下去的记忆到底写进了谁。
 *
 * 曾经踩到的坑：`ConsoleApp` 把**数字人模型 id** 当 companionId 传给了
 * 酒馆导入面板。模型没选时那个值是空串，于是面板退化成一个点不动的死界面
 * （只剩一句「请先选择一个陪伴对象」，而面板里没有任何可选项）。
 * 根因不是面板写错了，是「当前陪伴对象」在控制台里根本没有唯一来源。
 *
 * 解析优先级：用户偏好 > 部署声明（`default_persona_id`）> 清单首项。
 * 规则本身在 `./persona-scope`（纯函数、零依赖，可直接单测）；
 * 本文件只负责「拉清单 + 读偏好 + 落库」这些副作用。
 *
 * `refresh()` 的存在理由：创作工坊里新建 / 删除 / 改名角色后，清单必须重拉，
 * 否则「刚建好的角色在控制台里选不到」（而它在对话链路里其实已经可用）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getChatPreferences, getPersonas, setChatPreferences } from "@/lib/api/client";
import type { PersonaCatalog } from "@/lib/api/types";

import { resolvePersonaId } from "./persona-scope";

export interface PersonaScope {
  /** 当前陪伴对象 id（空串 = 清单还没回来） */
  personaId: string;
  catalog: PersonaCatalog | null;
  /** 清单是否还在加载（空 id + 加载中 ≠ 没有可选对象） */
  loading: boolean;
  /** 切换陪伴对象：本地立即生效，同时写后端（失败会抛出，由调用方提示） */
  select: (id: string) => Promise<void>;
  /**
   * 重新拉清单，并**校正当前选择**（创作工坊增删角色后调用）。
   *
   * 校正规则：当前选的角色还在 → 原样保留；已被删掉 → 按
   * 偏好 > 部署默认 > 首项 重新解析，并把结果写回偏好——
   * 不写回的话，Web 端与桌宠窗下次打开还会拿着那个不存在的 id 去对话（404）。
   */
  refresh: () => Promise<void>;
}

export function usePersonaScope(): PersonaScope {
  const [catalog, setCatalog] = useState<PersonaCatalog | null>(null);
  const [personaId, setPersonaId] = useState("");
  const [loading, setLoading] = useState(true);
  /** 当前选择的**同步**副本：refresh 里要拿它判断「还存不存在」（state 在闭包里是旧值） */
  const personaIdRef = useRef("");
  /** 卸载后不再写 state（refresh 是用户触发的异步调用，可能晚于卸载返回） */
  const aliveRef = useRef(true);

  useEffect(
    () => () => {
      aliveRef.current = false;
    },
    [],
  );

  /**
   * 拉清单并解析当前选择。
   *
   * `keepSelection` 区分两种进入方式：首屏要读**后端偏好**（三界面共享的那份），
   * 刷新时要保住**用户此刻的选择**（他刚在控制台切过的那个）。
   */
  const load = useCallback(async (keepSelection: boolean): Promise<void> => {
    const [next, preferences] = await Promise.all([getPersonas(), getChatPreferences()]);
    if (!aliveRef.current) return;

    const resolved = resolvePersonaId(
      next,
      keepSelection ? personaIdRef.current : (preferences?.persona_id ?? ""),
    );
    // 刷新时「解析结果 ≠ 原选择」= 原来那个角色已经不在清单里了
    const selectionWasStale = keepSelection && resolved !== personaIdRef.current;

    setCatalog(next);
    setPersonaId(resolved);
    personaIdRef.current = resolved;
    setLoading(false);

    if (selectionWasStale && resolved) {
      await setChatPreferences({ persona_id: resolved }).catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const select = useCallback(async (id: string): Promise<void> => {
    // 先本地生效：下拉要立刻反映用户的选择，不能等一个往返
    setPersonaId(id);
    personaIdRef.current = id;
    // 再写后端——控制台 / Web 端 / 桌宠窗三者 origin 不同，localStorage 不共享，
    // 「用哪个陪伴对象」只能由后端统一保管
    await setChatPreferences({ persona_id: id });
  }, []);

  const refresh = useCallback(() => load(true), [load]);

  return { personaId, catalog, loading, select, refresh };
}
