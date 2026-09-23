import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { PetDragPoint } from "../../shared/ipc";

/** 超过这个位移才认定是拖拽，避免把「点一下角色」误判成拖动窗口 */
const DRAG_THRESHOLD_PX = 4;

export interface WindowDragHandlers {
  onPointerDown(event: ReactPointerEvent<HTMLElement>): void;
  onPointerMove(event: ReactPointerEvent<HTMLElement>): void;
  onPointerUp(event: ReactPointerEvent<HTMLElement>): void;
  onPointerCancel(event: ReactPointerEvent<HTMLElement>): void;
  /** 是否正在拖拽（用于切换光标样式） */
  dragging: boolean;
}

/**
 * 桌宠窗拖拽。
 *
 * 两个关键点：
 *   1. 用 `event.screenX / screenY`（**屏幕坐标**）而不是 clientX / clientY
 *      ——拖拽时窗口本身在移动，窗口内相对坐标会随之漂移；
 *   2. 移动事件用 requestAnimationFrame 节流，一次屏幕刷新只跨进程发一次。
 *      主进程并不信任这个坐标（它会读真实光标位置），这里只是兜底。
 */
export function useWindowDrag(disabled: boolean): WindowDragHandlers {
  const [dragging, setDragging] = useState(false);
  const candidateRef = useRef<{ pointerId: number; position: PetDragPoint } | null>(null);
  const draggingRef = useRef(false);
  const pendingPointRef = useRef<PetDragPoint | null>(null);
  const frameRef = useRef<number | null>(null);
  /** 拖拽代数：结束时的收尾回调只对「当前这一轮」生效 */
  const generationRef = useRef(0);

  const flushPendingMove = useCallback(() => {
    frameRef.current = null;

    const point = pendingPointRef.current;
    pendingPointRef.current = null;

    if (!point || !draggingRef.current) {
      return;
    }

    window.hyprPet?.moveDrag(point);
  }, []);

  const queueMove = useCallback(
    (point: PetDragPoint) => {
      pendingPointRef.current = point;

      if (frameRef.current !== null) {
        return;
      }

      frameRef.current = window.requestAnimationFrame(flushPendingMove);
    },
    [flushPendingMove],
  );

  const resetDrag = useCallback(() => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }

    pendingPointRef.current = null;
    candidateRef.current = null;

    if (!draggingRef.current) {
      return;
    }

    draggingRef.current = false;
    generationRef.current += 1;
    setDragging(false);
  }, []);

  // 进入穿透 / 组件卸载时，不能让窗口停在「拖拽中」的状态
  useEffect(() => {
    if (disabled) {
      resetDrag();
    }
  }, [disabled, resetDrag]);

  useEffect(() => resetDrag, [resetDrag]);

  const onPointerDown = (event: ReactPointerEvent<HTMLElement>): void => {
    if (disabled || event.button !== 0 || draggingRef.current) {
      return;
    }

    candidateRef.current = {
      pointerId: event.pointerId,
      position: { x: event.screenX, y: event.screenY },
    };
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLElement>): void => {
    if (disabled) {
      return;
    }

    const candidate = candidateRef.current;

    if (!candidate || candidate.pointerId !== event.pointerId) {
      return;
    }

    if (!draggingRef.current) {
      const distance = Math.hypot(
        event.screenX - candidate.position.x,
        event.screenY - candidate.position.y,
      );

      if (distance <= DRAG_THRESHOLD_PX) {
        return;
      }

      draggingRef.current = true;
      generationRef.current += 1;
      setDragging(true);
      event.currentTarget.setPointerCapture(event.pointerId);
      window.hyprPet?.startDrag(candidate.position);
    }

    queueMove({ x: event.screenX, y: event.screenY });
  };

  const finishDrag = (event: ReactPointerEvent<HTMLElement>): void => {
    const candidate = candidateRef.current;
    candidateRef.current = null;

    if (!candidate || candidate.pointerId !== event.pointerId) {
      return;
    }

    if (!draggingRef.current) {
      return;
    }

    const generation = generationRef.current;

    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }

    pendingPointRef.current = null;
    draggingRef.current = false;
    setDragging(false);

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    // 先补一次终点位移，再让主进程收尾（夹进工作区 + 保存位置）
    window.hyprPet?.moveDrag({ x: event.screenX, y: event.screenY });

    if (generation === generationRef.current) {
      window.hyprPet?.endDrag();
    }
  };

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp: finishDrag,
    onPointerCancel: finishDrag,
    dragging,
  };
}
