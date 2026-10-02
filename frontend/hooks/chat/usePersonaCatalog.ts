"use client";

/**
 * 人设清单（`GET /chat/personas`）。
 *
 * 给「只关心当前角色是谁」的界面用：桌宠窗的气泡文案、控制台的记忆面板等。
 * Web 主页面不复用它——那里自己持有 catalog 状态（角色切换器要用，
 * 且切人设后要立刻重渲染），同一份清单在两处各拉一次、各自刷新反而更难对齐。
 *
 * 失败即返回 `null`（`getPersonas` 内部已吞掉错误），调用方用`personaName()`
 * 的中性兜底词，界面上不会出现「加载不出来就不显示」的空洞。
 */

import { useEffect, useState } from "react";

import { getPersonas } from "@/lib/api/client";
import type { PersonaCatalog } from "@/lib/api/types";

export function usePersonaCatalog(): PersonaCatalog | null {
  const [catalog, setCatalog] = useState<PersonaCatalog | null>(null);

  useEffect(() => {
    let active = true;
    void getPersonas().then((next) => {
      if (active) setCatalog(next);
    });
    return () => {
      active = false;
    };
  }, []);

  return catalog;
}
