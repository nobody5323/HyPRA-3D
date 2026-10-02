import { describe, expect, it } from "vitest";

import { MENU_MARGIN, placeContextMenu } from "../src/renderer/pet/pet-menu";

/**
 * 右键菜单落位。
 *
 * 守住的故障：菜单被窗口「截断」——桌宠窗是透明无边框的，菜单只能画在窗口内部
 * （`html, body { overflow: hidden }`），而窗口只比菜单宽一点点（280 vs 最小 188）。
 * 旧实现拿写死的 `188×26` 去夹取位置，既没乘 `--pet-scale` 也没算分隔线，
 * 于是在 105%（用户当前设置）下贴底右键，最后一项「退出 HyPRA」就被裁掉了。
 */

/** 桌宠窗可视区（缩放 100%）：见 petGeometry.ts 的 PET_BASE_SIZE */
const VIEWPORT = { width: 280, height: 480 };

/** 菜单实测尺寸（scale 1 时约 188×295：10 项 + 2 行 hint + 3 条分隔线） */
const MENU = { width: 188, height: 295 };

function place(
  cursor: { x: number; y: number },
  size = MENU,
  viewport = VIEWPORT,
) {
  return placeContextMenu({ cursor, size, viewport });
}

describe("placeContextMenu", () => {
  it("放得下时从光标右下展开（与系统菜单一致）", () => {
    expect(place({ x: 20, y: 20 })).toEqual({ left: 20, top: 20, maxHeight: null });
  });

  it("★ 右侧放不下就翻到光标左侧，而不是硬夹在窗口边缘", () => {
    const placement = place({ x: 260, y: 20 });

    expect(placement.left).toBe(260 - MENU.width);
    expect(placement.left + MENU.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  it("★ 下方放不下就翻到光标上方（回归：最后一项被裁掉）", () => {
    const placement = place({ x: 20, y: 460 });

    expect(placement.top).toBe(460 - MENU.height);
    expect(placement.top + MENU.height).toBeLessThanOrEqual(VIEWPORT.height);
  });

  it("两侧都放不下（窗口只比菜单宽 84px）时夹回窗口内", () => {
    // 光标 x=100：往右要 100+188+4=292 > 280，往左要 100-188 < 0，只能夹
    expect(place({ x: 100, y: 20 }).left).toBe(VIEWPORT.width - MENU.width - MENU_MARGIN);
  });

  it("窗口比菜单矮时改为内部滚动，而不是把菜单顶出窗口", () => {
    const short = { width: 280, height: 200 };
    const placement = place({ x: 20, y: 150 }, MENU, short);

    expect(placement.maxHeight).toBe(short.height - MENU_MARGIN * 2);
    expect(placement.top + (placement.maxHeight ?? 0)).toBeLessThanOrEqual(short.height);
  });

  it("★ 105% 缩放（菜单实际 197×310）下同样不溢出窗口", () => {
    const scaled = { width: MENU.width * 1.05, height: MENU.height * 1.05 };
    const placement = place({ x: 260, y: 460 }, scaled);

    expect(placement.left + scaled.width).toBeLessThanOrEqual(VIEWPORT.width);
    expect(placement.top + scaled.height).toBeLessThanOrEqual(VIEWPORT.height);
  });

  it("间隙可覆盖（单测用），默认值是 4px", () => {
    expect(MENU_MARGIN).toBe(4);
    expect(placeContextMenu({ cursor: { x: 0, y: 0 }, size: MENU, viewport: VIEWPORT, margin: 0 }).left).toBe(0);
  });
});
