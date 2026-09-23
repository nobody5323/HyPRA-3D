import { describe, expect, it } from "vitest";

import {
  applyDragDelta,
  clampBoundsToWorkArea,
  clampCoordinate,
  defaultPetBounds,
  PET_BASE_SIZE,
  parseSavedPosition,
  resizeBoundsForScale,
  scalePetSize,
  type Rect,
} from "../src/main/petGeometry";

/** 主屏工作区（1920×1080，任务栏占 40px） */
const WORK_AREA: Rect = { x: 0, y: 0, width: 1920, height: 1040 };

/** 右侧第二块屏：坐标从 1920 开始 */
const SECOND_WORK_AREA: Rect = { x: 1920, y: 0, width: 1280, height: 1024 };

describe("clampCoordinate", () => {
  it("区间内原样返回", () => {
    expect(clampCoordinate(50, 0, 100)).toBe(50);
  });

  it("越界时贴到边界", () => {
    expect(clampCoordinate(-10, 0, 100)).toBe(0);
    expect(clampCoordinate(130, 0, 100)).toBe(100);
  });

  it("区间反向时以 min 为准（不产生反着的区间）", () => {
    expect(clampCoordinate(50, 100, 0)).toBe(100);
  });
});

describe("clampBoundsToWorkArea", () => {
  it("完全在工作区内时不做改动", () => {
    const bounds: Rect = { x: 200, y: 300, width: 380, height: 480 };

    expect(clampBoundsToWorkArea(bounds, WORK_AREA)).toEqual(bounds);
  });

  it("拖出右侧时保留最小可见宽度", () => {
    const bounds: Rect = { x: 1900, y: 200, width: 380, height: 480 };
    const result = clampBoundsToWorkArea(bounds, WORK_AREA);

    expect(result.x).toBe(WORK_AREA.width - 80);
    expect(result.y).toBe(200);
  });

  it("拖到左上角外时保留最小可见高度与宽度", () => {
    const bounds: Rect = { x: -360, y: -440, width: 380, height: 480 };
    const result = clampBoundsToWorkArea(bounds, WORK_AREA);

    expect(result.x).toBe(-300); // 380 - 80
    expect(result.y).toBe(-400); // 480 - 80
  });

  it("允许桌宠停在屏幕边沿一半的位置（不强制完全在屏内）", () => {
    const bounds: Rect = { x: 1700, y: 900, width: 380, height: 480 };
    const result = clampBoundsToWorkArea(bounds, WORK_AREA);

    expect(result).toEqual(bounds);
  });
});

describe("scalePetSize", () => {
  it("按比例缩放并取整", () => {
    expect(scalePetSize(PET_BASE_SIZE, 0.7, WORK_AREA)).toEqual({ width: 266, height: 336 });
  });

  it("放大到超过工作区时等比退化为放得下的最大尺寸", () => {
    const smallArea: Rect = { x: 0, y: 0, width: 300, height: 400 };

    // 宽高必须保持同一比例（否则角色会被拉伸），因此取更严格的一边：300/380
    expect(scalePetSize(PET_BASE_SIZE, 1.5, smallArea)).toEqual({ width: 300, height: 379 });
  });

  it("缩放后宽高比与基准尺寸一致", () => {
    const scaled = scalePetSize(PET_BASE_SIZE, 1.25, WORK_AREA);

    expect(scaled.width / scaled.height).toBeCloseTo(
      PET_BASE_SIZE.width / PET_BASE_SIZE.height,
      2,
    );
  });
});

describe("defaultPetBounds", () => {
  it("停在工作区右下角并留出边距", () => {
    const bounds = defaultPetBounds(WORK_AREA);

    expect(bounds.width).toBe(PET_BASE_SIZE.width);
    expect(bounds.height).toBe(PET_BASE_SIZE.height);
    expect(bounds.x + bounds.width).toBe(WORK_AREA.width - 28);
    expect(bounds.y + bounds.height).toBe(WORK_AREA.height - 28);
  });

  it("在第二块屏上按该屏的右下角停靠", () => {
    const bounds = defaultPetBounds(SECOND_WORK_AREA);

    expect(bounds.x + bounds.width).toBe(SECOND_WORK_AREA.x + SECOND_WORK_AREA.width - 28);
  });
});

describe("parseSavedPosition", () => {
  it("读取合法位置", () => {
    expect(parseSavedPosition({ x: 120, y: 340 })).toEqual({ x: 120, y: 340 });
  });

  it("小数会被取整", () => {
    expect(parseSavedPosition({ x: 120.6, y: 340.2 })).toEqual({ x: 121, y: 340 });
  });

  it("非法值一律视作没有存档", () => {
    expect(parseSavedPosition(undefined)).toBeUndefined();
    expect(parseSavedPosition(null)).toBeUndefined();
    expect(parseSavedPosition("120,340")).toBeUndefined();
    expect(parseSavedPosition({ x: 120 })).toBeUndefined();
    expect(parseSavedPosition({ x: Number.NaN, y: 0 })).toBeUndefined();
    expect(parseSavedPosition({ x: Number.POSITIVE_INFINITY, y: 0 })).toBeUndefined();
    expect(parseSavedPosition({ x: "120", y: 340 })).toBeUndefined();
  });
});

describe("applyDragDelta", () => {
  it("位移等于光标位移", () => {
    expect(applyDragDelta({ x: 100, y: 200 }, { x: 500, y: 500 }, { x: 530, y: 460 })).toEqual({
      x: 130,
      y: 160,
    });
  });

  it("结果取整（避免窗口停在半个像素上）", () => {
    expect(applyDragDelta({ x: 0, y: 0 }, { x: 0.4, y: 0.4 }, { x: 10.7, y: 10.7 })).toEqual({
      x: 10,
      y: 10,
    });
  });
});

describe("resizeBoundsForScale", () => {
  const current: Rect = { x: 1400, y: 500, width: 380, height: 480 };

  it("以底边中点为锚点缩放（桌宠的脚不动）", () => {
    const grown = resizeBoundsForScale(current, WORK_AREA, 1.5);

    expect(grown.width).toBe(570);
    expect(grown.height).toBe(720);
    // 底边中点不变
    expect(grown.x + grown.width / 2).toBeCloseTo(current.x + current.width / 2, 0);
    expect(grown.y + grown.height).toBe(current.y + current.height);
  });

  it("缩小后同样保持底边中点", () => {
    const shrunk = resizeBoundsForScale(current, WORK_AREA, 0.7);

    expect(shrunk.width).toBe(266);
    expect(shrunk.x + shrunk.width / 2).toBeCloseTo(current.x + current.width / 2, 0);
  });

  it("放大后溢出屏幕时仍保留可见区域", () => {
    const atEdge: Rect = { x: 1800, y: 900, width: 380, height: 480 };
    const grown = resizeBoundsForScale(atEdge, WORK_AREA, 1.5);

    expect(grown.x).toBeLessThanOrEqual(WORK_AREA.width - 80);
    expect(grown.y).toBeLessThanOrEqual(WORK_AREA.height - 80);
  });
});
