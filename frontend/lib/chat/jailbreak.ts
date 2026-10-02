/**
 * 叙事框架（jailbreak）层的取值解析 —— 三端共用一份，避免各自实现漂移。
 *
 * ## 为什么需要这个函数
 *
 * 后端偏好里的 `jailbreak_id` 有三种取值，语义**不同**：
 *
 * | 取值      | 含义                     | 界面应显示       |
 * |-----------|--------------------------|------------------|
 * | `""`      | 没选过 → 跟随部署默认    | 部署默认那一档（可能是「不使用」） |
 * | `"none"`  | 用户明确关掉             | 不使用           |
 * | 具体 id   | 用户选定该档             | 该档             |
 *
 * 「未设置」要**解析成真实生效的那一档**再显示。若一律显示成「不使用」，
 * 部署方把默认设成开启时界面就在对用户说谎——他以为没开，实际每轮都在开。
 *
 * 这个解析被三处用到（Web 端页面、程序控制台、桌宠窗），任何一处自己写一遍
 * 都可能与另两处不一致，所以收敛到这里。
 */

import type { JailbreakCatalog } from "@/lib/api/types";

/** 关闭本层的特殊值（后端约定，见 `app/api/chat.py` 的 `ChatPreferencesPatch`） */
export const JAILBREAK_NONE = "none";

/**
 * 把偏好里的原始值解析成**界面应显示 / 生效**的档位 id。
 *
 * @param value   偏好里的原始值（`""` = 未设置，`"none"` = 显式关闭，其它 = 具体档）
 * @param catalog 后端清单 + 部署默认；`null`（离线）时一律按「不使用」显示
 */
export function resolveJailbreakId(
  value: string,
  catalog: JailbreakCatalog | null,
): string {
  if (value) return value;
  if (!catalog) return JAILBREAK_NONE;
  // 部署说开启但没指定档位时，后端会回落到最保守的内置档；这里只需保证
  // 「开启状态」能显示成一个具体档位，空 id 不能直接透出去（会渲染成空选择）
  return catalog.enabled
    ? catalog.default_jailbreak_id || JAILBREAK_NONE
    : JAILBREAK_NONE;
}
