/**
 * 锚定浮层的定位。
 *
 * 这个 hook 存在的唯一理由是「`absolute` 浮层会被祖先的 `overflow` 裁掉」——
 * 程序控制台的 `Card` 就是 `overflow-hidden`，四个切换器的下拉因此只露一条缝。
 * 所以这里守的是两件事：**坐标真的按触发器算**，以及**下方不够时会翻上去**。
 *
 * jsdom 没有布局引擎（`getBoundingClientRect` 恒返回全 0），所以下面显式桩掉
 * 触发器的 rect 与视口尺寸——这恰恰是本 hook 的全部输入，桩掉之后就是纯函数断言。
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { useRef } from "react";

import { useAnchoredPopup } from "@/components/settings/useAnchoredPopup";

/** 触发器的矩形（只用到这四个字段） */
type Rect = { top: number; bottom: number; left: number; width: number };

const ZERO_RECT = {
  x: 0,
  y: 0,
  top: 0,
  bottom: 0,
  left: 0,
  right: 0,
  width: 0,
  height: 0,
  toJSON: () => ({}),
} as DOMRect;

function Harness({ rect, open = true }: { rect: Rect; open?: boolean }) {
  const ref = useRef<HTMLButtonElement>(null);
  const style = useAnchoredPopup(ref, open);

  return (
    <>
      <button
        ref={(node) => {
          ref.current = node;
          if (node) {
            node.getBoundingClientRect = () => ({ ...ZERO_RECT, ...rect });
          }
        }}
        type="button"
      >
        触发器
      </button>
      <div data-testid="popup" style={style} />
    </>
  );
}

/** 把浮层的内联样式读成普通对象（jsdom 会原样保留 React 写入的值） */
function popupStyle(rect: Rect, open = true): Record<string, string> {
  // 一个用例里可能测多种矩形，先清掉上一次的渲染，否则 getByTestId 会撞到多个
  cleanup();
  const { getByTestId } = render(<Harness rect={rect} open={open} />);
  const node = getByTestId("popup") as HTMLElement;
  return {
    position: node.style.position,
    top: node.style.top,
    bottom: node.style.bottom,
    left: node.style.left,
    width: node.style.width,
    maxHeight: node.style.maxHeight,
  };
}

const VIEWPORT_HEIGHT = 800;
const VIEWPORT_WIDTH = 1024;

beforeEach(() => {
  Object.defineProperty(window, "innerHeight", {
    value: VIEWPORT_HEIGHT,
    configurable: true,
  });
  Object.defineProperty(window, "innerWidth", {
    value: VIEWPORT_WIDTH,
    configurable: true,
  });
});

afterEach(cleanup);

describe("useAnchoredPopup", () => {
  it("★ 用 fixed 定位（这是逃逸祖先 overflow 裁剪的关键）", () => {
    const style = popupStyle({ top: 100, bottom: 144, left: 40, width: 300 });

    expect(style.position).toBe("fixed");
  });

  it("下方空间充足时贴在触发器下沿，宽度与触发器一致", () => {
    const style = popupStyle({ top: 100, bottom: 144, left: 40, width: 300 });

    expect(style.top).toBe("148px"); // bottom + 4px 间距
    expect(style.left).toBe("40px");
    expect(style.width).toBe("300px");
    expect(style.bottom).toBe("");
  });

  it("★ 下方不够（控制台最小高度下就是这样）时翻到触发器上方", () => {
    Object.defineProperty(window, "innerHeight", { value: 400, configurable: true });
    const style = popupStyle({ top: 300, bottom: 344, left: 40, width: 300 });

    // 下方只剩 44px，上方有 288px → 翻上去，用 bottom 而不是 top
    expect(style.top).toBe("");
    expect(style.bottom).toBe("104px"); // innerHeight - top + 4px
    expect(style.maxHeight).toBe("288px");
  });

  it("maxHeight 跟着可用空间走，且不超过上限", () => {
    const roomy = popupStyle({ top: 100, bottom: 144, left: 40, width: 300 });
    expect(roomy.maxHeight).toBe("420px"); // 上限，不因为窗口高就无限拉长

    Object.defineProperty(window, "innerHeight", { value: 200, configurable: true });
    const cramped = popupStyle({ top: 90, bottom: 134, left: 0, width: 100 });
    expect(cramped.maxHeight).toBe("140px"); // 下限：至少露出一截，靠内部滚动看
  });

  it("触发器贴右边缘时把浮层夹回视口内", () => {
    // 900 + 300 = 1200 > 1024 的视口 → 夹到 1024 - 300 - 8
    const style = popupStyle({ top: 10, bottom: 54, left: 900, width: 300 });

    expect(style.left).toBe("716px");
  });

  it("关闭时不带任何坐标（下次打开重新测）", () => {
    const style = popupStyle({ top: 100, bottom: 144, left: 40, width: 300 }, false);

    // 未测量时用 absolute + hidden 占位：不参与流式布局，也看不见
    expect(style.position).toBe("absolute");
    expect(style.top).toBe("");
    expect(style.maxHeight).toBe("");
  });
});

/**
 * 接线回归：四个切换器必须都走这个 hook。
 *
 * 断源码而不是渲染结果——渲染断言查不出「用了 absolute 但恰好没被裁」这种情况，
 * 而真被裁时（控制台的 `Card overflow-hidden`）用户看到的是「点不开」，
 * 离这里很远，很难回溯。这组断言把「不许退回 absolute」钉在原地。
 */
describe("四个切换器的浮层定位", () => {
  const DIR = path.join(process.cwd(), "components/settings");
  const SWITCHERS = [
    "PersonaSwitcher.tsx",
    "StyleSwitcher.tsx",
    "PresetSwitcher.tsx",
    "JailbreakSwitcher.tsx",
  ];

  it.each(SWITCHERS)("%s 用 useAnchoredPopup 定位", (name) => {
    const source = fs.readFileSync(path.join(DIR, name), "utf8");

    expect(source).toContain("useAnchoredPopup");
    expect(source).toContain("style={popupStyle}");
  });

  it.each(SWITCHERS)("%s 没有退回 `absolute ... mt-1 w-full`", (name) => {
    const source = fs.readFileSync(path.join(DIR, name), "utf8");

    // 这正是被 Card 的 overflow-hidden 裁掉的那种写法
    expect(source).not.toContain("absolute z-20 mt-1 w-full");
  });
});
