/**
 * 桌宠外壳的两条产品规则（抽成纯函数，便于单测钉住）。
 *
 * 与 `avatar-source.ts` 同一个理由：规则一旦散在组件与 CSS 里，
 * 「为什么这行字还在」就只能靠肉眼看，改一次错一次。
 */

/** 渲染阶段（由 `AvatarSurface` 判定并上报） */
export type PetLoadStage = "loading" | "ready" | "failed";

/**
 * 底部状态文字。
 *
 * 规则：**就绪后清空**。模型/立绘已经看得见了，再常挂一行
 * 「正在加载 Live2D 模型：模型已就绪：N 个参数…」只是噪音（用户报的就是它）。
 *
 * 失败与降级保留文本：Live2D 加载失败、立绘拉不到、未安装 Cubism SDK 而降级——
 * 这些是用户**需要知道的结果**，不是过程；清了就只剩「怎么没渲染出来」的困惑。
 */
export function resolveStatusText(stage: PetLoadStage, detail: string): string {
  if (stage === "failed") {
    return detail || "渲染失败";
  }

  if (stage === "loading") {
    return detail || "正在检测渲染环境…";
  }

  return "";
}

/**
 * 底部工具条是否显示。
 *
 * 默认隐藏，只在「指针位于桌宠（角色本体或工具条）上」或「浮层打开」时出现：
 * 常挂三个按钮会一直在桌宠身下晃，而窗口只有 280×480。
 *
 * 浮层打开时**保持可见**：鼠标一进聊天面板或设置面板，`:hover` 就已经离开了桌宠，
 * 若跟着隐藏，「收起 / 穿透 / 隐藏」会在用户正要用的那一刻消失。
 */
export function resolveControlsVisible(input: {
  pointerInside: boolean;
  overlayOpen: boolean;
}): boolean {
  return input.pointerInside || input.overlayOpen;
}
