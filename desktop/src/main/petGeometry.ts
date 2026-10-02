/**
 * 桌宠窗的几何计算（纯函数，无 Electron 依赖，便于单测）。
 *
 * 坐标一律是**屏幕坐标**（CSS 像素、含多显示器虚拟桌面偏移）。
 */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

/**
 * 桌宠窗基准尺寸（缩放 100% 时）。
 *
 * **宽度贴着角色收紧过**（原先 380 → 现在 280）。角色本体只有 200×200、
 * 魔珐竖屏容器约 209×372（9:16、高撑满内容区），380 宽的窗口左右各空 80+px：
 * 那片透明区平时看不见，却照样吃鼠标事件——非穿透模式下会挡住下层窗口的点击，
 * 右键也会在离角色很远的地方弹出菜单。
 *
 * **高度保持 480 不动**。角色区高 372 不是写死的，而是「窗口高 − 上下留白」
 * （`--pet-stage-top` / `--pet-stage-bottom`，见 pet.css）：顶部 56px 留给字幕气泡、
 * 底部 52px 留给状态条与工具条。压矮窗口 = 压矮角色，或让气泡压到角色脸上，
 * 而这次要的恰恰是「角色不变」。
 *
 * ⚠️ 再往下收宽度前先看两个约束：① 右键菜单最小 188px（`pet.css` 的 `.petMenu`），
 * 窗口窄于 196px 时它就只能靠内部滚动；② 对话面板展开是往右加宽一栏，
 * 模型区锁在左侧原宽度，因此模型区的宽度就等于这里的 width。
 */
export const PET_BASE_SIZE: Size = { width: 280, height: 480 };

/** 默认停靠时与工作区边缘的距离 */
export const PET_EDGE_MARGIN = 28;

/** 窗口被拖到屏幕外时，至少保留这么多像素可见，否则用户找不回来 */
export const PET_MIN_VISIBLE = 80;

/** 把数值夹进 [min, max]；当 max < min 时以 min 为准（避免出现反向区间） */
export function clampCoordinate(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * 保证窗口至少有 `minVisible` 像素落在工作区内。
 *
 * 多显示器场景下，窗口可能被拖到某个屏幕的边缘之外；这里只做「可见性兜底」，
 * 不强制窗口完全进入工作区（用户可以把桌宠停在屏幕边沿一半的位置）。
 */
export function clampBoundsToWorkArea(
  bounds: Rect,
  workArea: Rect,
  minVisible: number = PET_MIN_VISIBLE,
): Rect {
  const visibleWidth = Math.min(bounds.width, minVisible);
  const visibleHeight = Math.min(bounds.height, minVisible);

  return {
    ...bounds,
    x: clampCoordinate(
      bounds.x,
      workArea.x - bounds.width + visibleWidth,
      workArea.x + workArea.width - visibleWidth,
    ),
    y: clampCoordinate(
      bounds.y,
      workArea.y - bounds.height + visibleHeight,
      workArea.y + workArea.height - visibleHeight,
    ),
  };
}

/**
 * 按缩放比例换算窗口尺寸，且不超过工作区。
 *
 * 缩放后的窗口若大于工作区，会退化到能放下的最大尺寸——否则桌宠会盖满整块屏幕。
 */
export function scalePetSize(baseSize: Size, scale: number, workArea: Rect): Size {
  const safeScale = Math.max(0.1, scale);
  const effectiveScale = Math.min(
    safeScale,
    workArea.width / baseSize.width,
    workArea.height / baseSize.height,
  );

  return {
    width: Math.max(1, Math.round(baseSize.width * effectiveScale)),
    height: Math.max(1, Math.round(baseSize.height * effectiveScale)),
  };
}

/** 默认位置：工作区右下角，与边缘保持 `PET_EDGE_MARGIN` */
export function defaultPetBounds(workArea: Rect, scale = 1): Rect {
  const size = scalePetSize(PET_BASE_SIZE, scale, workArea);

  return clampBoundsToWorkArea(
    {
      x: Math.round(workArea.x + workArea.width - size.width - PET_EDGE_MARGIN),
      y: Math.round(workArea.y + workArea.height - size.height - PET_EDGE_MARGIN),
      ...size,
    },
    workArea,
  );
}

/**
 * 解析持久化下来的窗口位置。
 *
 * 位置文件是用户可手改的普通 JSON，非法值（缺字段 / NaN / 字符串）一律当作「没存过」，
 * 让窗口回到默认右下角，而不是把窗口创建在 NaN 坐标上。
 */
export function parseSavedPosition(value: unknown): { x: number; y: number } | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }

  const candidate = value as { x?: unknown; y?: unknown };

  if (!Number.isFinite(candidate.x) || !Number.isFinite(candidate.y)) {
    return undefined;
  }

  return { x: Math.round(candidate.x as number), y: Math.round(candidate.y as number) };
}

/** 拖拽位移：起点窗口位置 + （当前光标 − 起点光标） */
export function applyDragDelta(
  origin: { x: number; y: number },
  startPointer: { x: number; y: number },
  currentPointer: { x: number; y: number },
): { x: number; y: number } {
  return {
    x: Math.round(origin.x + currentPointer.x - startPointer.x),
    y: Math.round(origin.y + currentPointer.y - startPointer.y),
  };
}

/**
 * 缩放时重算窗口位置与尺寸。
 *
 * 锚点取**底边中点**：桌宠站在桌面上，缩放时「脚不动」比「左上角不动」更自然，
 * 也不会因为变大而把角色推到底部屏幕外。缩小后同样会夹回工作区可见范围。
 */
export function resizeBoundsForScale(current: Rect, workArea: Rect, scale: number): Rect {
  const size = scalePetSize(PET_BASE_SIZE, scale, workArea);
  const anchorCenterX = current.x + current.width / 2;
  const anchorBottomY = current.y + current.height;

  return clampBoundsToWorkArea(
    {
      x: Math.round(anchorCenterX - size.width / 2),
      y: Math.round(anchorBottomY - size.height),
      ...size,
    },
    workArea,
  );
}
