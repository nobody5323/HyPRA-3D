import { describe, expect, it } from "vitest";

import { createTrayIconPng, encodePng, renderTrayIconPixels } from "../src/main/trayIcon";

/** PNG 文件头：89 50 4E 47 0D 0A 1A 0A */
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

describe("encodePng", () => {
  it("产物是合法 PNG（签名 + IHDR）", () => {
    const png = createTrayIconPng(8);

    expect([...png.subarray(0, 8)]).toEqual(PNG_SIGNATURE);
    expect(png.subarray(12, 16).toString("ascii")).toBe("IHDR");
    expect(png.readUInt32BE(16)).toBe(8); // 宽
    expect(png.readUInt32BE(20)).toBe(8); // 高
    expect(png[24]).toBe(8); // 位深
    expect(png[25]).toBe(6); // RGBA
  });

  it("以 IEND 收尾（缺了它部分解码器会拒绝整张图）", () => {
    const png = createTrayIconPng(8);

    expect(png.subarray(png.length - 8, png.length - 4).toString("ascii")).toBe("IEND");
  });

  it("尺寸随参数变化（高 DPI 托盘需要更大图标）", () => {
    expect(createTrayIconPng(16).readUInt32BE(16)).toBe(16);
    expect(createTrayIconPng(32).readUInt32BE(20)).toBe(32);
  });

  it("相同的输入得到相同的字节（构建可复现）", () => {
    expect(createTrayIconPng(16).equals(createTrayIconPng(16))).toBe(true);
  });

  it("像素数据长度必须是宽 × 高 × 4", () => {
    expect(encodePng(4, 4, Buffer.alloc(4 * 4 * 4)).length).toBeGreaterThan(0);
  });
});

describe("renderTrayIconPixels", () => {
  const size = 16;
  const pixels = renderTrayIconPixels(size);

  /** 取某像素的 RGBA */
  const pixelAt = (x: number, y: number): number[] => {
    const offset = (y * size + x) * 4;

    return [...pixels.subarray(offset, offset + 4)];
  };

  it("中心是不透明的暖色", () => {
    const [r, g, b, a] = pixelAt(size / 2, size / 2);

    expect(a).toBe(255);
    expect(r).toBe(0xe0);
    expect(g).toBe(0xa2);
    expect(b).toBe(0x4e);
  });

  it("四角透明（否则托盘上会显示成一个方块）", () => {
    expect(pixelAt(0, 0)[3]).toBe(0);
    expect(pixelAt(size - 1, 0)[3]).toBe(0);
    expect(pixelAt(0, size - 1)[3]).toBe(0);
    expect(pixelAt(size - 1, size - 1)[3]).toBe(0);
  });
});
