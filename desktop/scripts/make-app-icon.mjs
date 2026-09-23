/**
 * 生成应用图标（256×256 PNG）。
 *
 * 桌面快捷方式与安装包需要 ≥256px 的图标，electron-builder 会据此生成 .ico。
 * 构建时生成而不入库：仓库里少一个二进制文件，评审也能直接看懂图标长什么样。
 *
 * 注意：这里的 PNG 编码与像素绘制是 `src/main/trayIcon.ts`（托盘图标）的**同源实现**。
 * 之所以没有直接复用它，是因为本脚本由 Node 直接执行，而那份代码是给 tsc 编译的
 * TypeScript（Node 按包类型判定模块系统，两边不能互相 import）。
 * 两边改动时请一起改——托盘图标与应用图标应当始终一致。
 */
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outputDirectory = path.join(desktopRoot, "assets");
const outputPath = path.join(outputDirectory, "icon.png");
const SIZE = 256;

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);

  for (let index = 0; index < 256; index += 1) {
    let value = index;

    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }

    table[index] = value >>> 0;
  }

  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;

  for (const byte of buffer) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }

  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, payload) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(payload.length, 0);

  const body = Buffer.concat([Buffer.from(type, "ascii"), payload]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);

  return Buffer.concat([length, body, crc]);
}

/** 暖色圆点 + 左上高光（与桌宠形象同一色系） */
function renderPixels(size) {
  const rgba = Buffer.alloc(size * size * 4);
  const center = (size - 1) / 2;
  const radius = size / 2 - size / 32;
  const highlightX = center - size * 0.18;
  const highlightY = center - size * 0.2;
  const highlightRadius = size * 0.12;

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const distance = Math.hypot(x - center, y - center);
      const coverage = Math.min(1, Math.max(0, radius - distance + 0.5));
      const offset = (y * size + x) * 4;
      const isHighlight = Math.hypot(x - highlightX, y - highlightY) < highlightRadius;

      rgba[offset] = isHighlight ? 0xff : 0xe0;
      rgba[offset + 1] = isHighlight ? 0xfa : 0xa2;
      rgba[offset + 2] = isHighlight ? 0xf2 : 0x4e;
      rgba[offset + 3] = Math.round(coverage * 255);
    }
  }

  return rgba;
}

function encodePng(size, rgba) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.writeUInt8(8, 8);
  header.writeUInt8(6, 9);

  const stride = size * 4;
  const raw = Buffer.alloc((stride + 1) * size);

  for (let row = 0; row < size; row += 1) {
    raw[row * (stride + 1)] = 0;
    rgba.copy(raw, row * (stride + 1) + 1, row * stride, (row + 1) * stride);
  }

  return Buffer.concat([
    PNG_SIGNATURE,
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

await mkdir(outputDirectory, { recursive: true });
await writeFile(outputPath, encodePng(SIZE, renderPixels(SIZE)));

console.log(`已生成应用图标：${path.relative(desktopRoot, outputPath)}（${SIZE}×${SIZE}）`);
