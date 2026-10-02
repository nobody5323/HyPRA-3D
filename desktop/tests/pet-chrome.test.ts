import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { resolveControlsVisible, resolveStatusText } from "../src/renderer/pet/pet-chrome";

/**
 * 桌宠外壳的两条易错规则：
 *
 * 1. **底部状态文字在就绪后必须消失**——原先只要 `detail` 有值就一直挂着，而
 *    Live2D 渲染器在**就绪时也会**上报一句「模型已就绪：N 个参数…」，
 *    于是「正在加载模型…」永远留在桌宠下方（用户报的就是这条）；
 * 2. **工具条默认隐藏**——只在指针位于桌宠或浮层打开时出现，且隐藏的方式不能
 *    把布局抽掉（否则鼠标一移过去按钮就消失，点不到）。
 */

const PET_CSS_PATH = fileURLToPath(new URL("../src/renderer/pet/pet.css", import.meta.url));

describe("resolveStatusText", () => {
  it("就绪后清空——不管 detail 里还留着什么诊断文本", () => {
    expect(resolveStatusText("ready", "模型已就绪：62 个参数，缺少 0 个")).toBe("");
    expect(resolveStatusText("ready", "")).toBe("");
  });

  it("加载中给出进度文本，没有 detail 时给兜底文案", () => {
    expect(resolveStatusText("loading", "正在加载模型：/live2d/xxx.model3.json")).toBe(
      "正在加载模型：/live2d/xxx.model3.json",
    );
    expect(resolveStatusText("loading", "")).toBe("正在检测渲染环境…");
  });

  it("失败/降级保留原因（那是用户需要知道的结果，不是过程）", () => {
    expect(resolveStatusText("failed", "未安装 Cubism SDK")).toBe("未安装 Cubism SDK");
    expect(resolveStatusText("failed", "")).toBe("渲染失败");
  });
});

describe("resolveControlsVisible", () => {
  it("默认隐藏", () => {
    expect(resolveControlsVisible({ pointerInside: false, overlayOpen: false })).toBe(false);
  });

  it("指针在桌宠上时显示", () => {
    expect(resolveControlsVisible({ pointerInside: true, overlayOpen: false })).toBe(true);
  });

  it("浮层打开时保持显示（否则鼠标一进聊天面板，「收起」就没了）", () => {
    expect(resolveControlsVisible({ pointerInside: false, overlayOpen: true })).toBe(true);
  });
});

describe("pet.css 的工具条显隐规则", () => {
  const css = readFileSync(PET_CSS_PATH, "utf8");

  it("默认是 opacity:0 + visibility:hidden（隐藏但**不**抽掉布局）", () => {
    const block = css.match(/\.petToolbar\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(block).toContain("opacity: 0");
    expect(block).toContain("visibility: hidden");
    // display:none 会让 .petDock 高度塌掉：鼠标移过去时命中区消失，按钮点不到
    expect(block).not.toContain("display: none");
  });

  it("只有 data-controls=\"show\" 时才显示", () => {
    expect(css).toContain('.petShell[data-controls="show"] .petToolbar');
  });
});
