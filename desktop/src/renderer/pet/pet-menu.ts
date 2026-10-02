/**
 * 右键菜单的落位规则（纯函数，便于单测钉住）。
 *
 * 为什么单独抽出来：桌宠窗是**透明无边框**的，菜单只能画在窗口内部，
 * 而窗口只比菜单大一点（280 宽 vs 菜单最小 188），贴边右键时菜单必然顶出去，
 * 被 `html, body { overflow: hidden }` 裁掉一截——用户看到的就是
 * 「右键菜单被范围截断」，最下面那项（退出 HyPRA）直接不见。
 *
 * 这里**不估算尺寸**。菜单高度取决于 hint 行数、字号、分隔线，以及
 * `--pet-scale`（缩放设置），四样都会变；旧实现拿 `188×26` 当常量去夹取，
 * 既没乘缩放也没算分隔线，105% 下就已经短了约 30px（这就是那个 bug 的根因）。
 * 调用方把**实测尺寸**（`getBoundingClientRect()`）传进来，这里只负责算落点。
 */

/** 菜单与窗口边缘之间留的最小间隙 */
export const MENU_MARGIN = 4;

export interface MenuPlacementInput {
  /** 右键时的光标位置（窗口内坐标，即 clientX / clientY） */
  cursor: { x: number; y: number };
  /** 菜单实测尺寸 */
  size: { width: number; height: number };
  /** 窗口可视区尺寸（window.innerWidth / window.innerHeight） */
  viewport: { width: number; height: number };
  /** 覆盖默认间隙（单测用） */
  margin?: number;
}

export interface MenuPlacement {
  left: number;
  top: number;
  /**
   * 需要内部滚动时的最大高度；`null` = 装得下，不用滚。
   *
   * 这是最后一道保险：窗口矮于菜单时（比如今后把窗口改得更小、
   * 或用户在系统里把缩放调得很大），宁可让菜单自己滚，也不能让它顶出窗口被裁。
   */
  maxHeight: number | null;
}

/**
 * 单轴落位：先顺着光标方向放，放不下就翻到光标另一侧，两侧都放不下才夹回窗口内。
 *
 * 与系统原生菜单同一套行为——右键的位置决定菜单朝哪边展开，而不是被硬塞到角落。
 * 夹回窗口内时宁可压住光标（`Math.min(cursor, ...)`），也不让内容跑到窗口外。
 */
function placeAxis(cursor: number, size: number, viewport: number, margin: number): number {
  // 顺着光标方向放得下：直接用光标位置（菜单从光标右下展开）
  if (cursor + size + margin <= viewport) {
    return cursor;
  }

  // 放不下就翻到光标另一侧（向上 / 向左展开）
  const flipped = cursor - size;

  if (flipped >= margin) {
    return flipped;
  }

  // 两侧都放不下（窗口只比菜单大一点点）：夹进窗口，别让内容被裁掉
  return Math.max(margin, Math.min(cursor, viewport - size - margin));
}

/** 算菜单落点与滚动上限（输入输出都是 CSS 像素，缩放已体现在实测尺寸里） */
export function placeContextMenu(input: MenuPlacementInput): MenuPlacement {
  const margin = input.margin ?? MENU_MARGIN;
  const available = input.viewport.height - margin * 2;
  const maxHeight = input.size.height > available ? Math.max(0, available) : null;
  // 落位按**滚动后**的高度算：菜单真的滚起来时，占的高度就是 maxHeight
  const height = maxHeight ?? input.size.height;

  return {
    left: placeAxis(input.cursor.x, input.size.width, input.viewport.width, margin),
    top: placeAxis(input.cursor.y, height, input.viewport.height, margin),
    maxHeight,
  };
}
