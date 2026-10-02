import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 防回归：桌宠窗的尺寸**不许**沿用 `getBounds()` 读回的值。
 *
 * 背景（真实故障）：在 150% 缩放的屏幕上，Electron 的窗口尺寸会在**每次移动后 +1 DIP**
 * —— 当年请求 380×480（那时的基准尺寸），读回就已经是 382×482，之后每动一次再 +1。
 * 而 `movePetWindowDrag` 当时写的是 `width: bounds.width`（把读回值原样写回去），
 * 于是**每次拖动都变大一点**；`scale` 恒为 1.0，所以设置面板一直显示「100%」，
 * 用户只能看到桌宠在无端膨胀（拖 12 次后量到 392×492）。
 *
 * 修法：尺寸一律用 `expectedPetSize()` 按 `PET_BASE_SIZE × scale` **重算**
 * （实测 12 次移动后稳定在请求的尺寸）。
 *
 * 为什么用源码扫描而不是行为单测：真实漂移需要 Electron 窗口 + 高 DPI 环境，
 * vitest 的 node 环境复现不了；把**写法**钉住是这里性价比最高的防线。
 * 手工验证工具见 `_local/win-probe-drag.cjs`（不入库）。
 */
describe("桌宠窗尺寸的防回归", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../src/main/petWindow.ts", import.meta.url)),
    "utf8",
  );

  it("写入窗口 bounds 时不得沿用读回的尺寸", () => {
    // 抓出所有 setBounds(...) 调用（可能跳行），断言里面没有把**当前尺寸**写回去。
    //
    // 不能只搜 `width: bounds.width`：`windowRect()` 里组装 Rect 时本来就会这么写
    // （那是读，不是写回），拿它当判据会误报。
    const calls = source.match(/setBounds\([\s\S]*?\);/g) ?? [];

    expect(calls.length).toBeGreaterThan(0);

    for (const call of calls) {
      expect(call).not.toMatch(/bounds\.width/);
      expect(call).not.toMatch(/bounds\.height/);
    }
  });

  it("尺寸来自理论值（expectedPetSize）", () => {
    expect(source).toContain("function expectedPetSize");
    // 尺寸必须由 PET_BASE_SIZE × scale 算出来（对话面板展开时可以再加一栏，
    // 但绝不能在读回的 bounds 上加加减减）
    expect(source).toMatch(/scalePetSize\(\s*base[,\s]/);
  });

  it("移动窗口的三条路径都做了尺寸纠正", () => {
    // 拖动中 / 拖动结束 / 显示器变化——三条都会写 bounds，都要纠正
    const occurrences = source.match(/expectedPetSize\(/g) ?? [];

    // 1 处定义 + 3 处调用
    expect(occurrences.length).toBeGreaterThanOrEqual(4);
  });
});
