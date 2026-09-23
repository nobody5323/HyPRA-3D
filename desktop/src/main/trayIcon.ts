import zlib from "node:zlib";

/**
 * 运行时生成托盘图标。
 *
 * 为什么不用图片文件：托盘图标是一个 32×32 的纯色圆点，为此往仓库里塞一个二进制
 * 资产并不划算（评审看仓库时无法 review，也容易被误当成素材）。这里直接按 PNG
 * 规范编码一份，可复现、可单测、零二进制文件。
 */

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** CRC32 查表（PNG 每个 chunk 都要带 CRC） */
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

function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;

  for (const byte of buffer) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }

  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type: string, payload: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(payload.length, 0);

  const body = Buffer.concat([Buffer.from(type, "ascii"), payload]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);

  return Buffer.concat([length, body, crc]);
}

/** 把 RGBA 像素编码成 PNG（8 位真彩 + alpha，无隔行） */
export function encodePng(width: number, height: number, rgba: Buffer): Buffer {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.writeUInt8(8, 8); // 位深
  header.writeUInt8(6, 9); // 颜色类型 6 = RGBA
  header.writeUInt8(0, 10); // 压缩方式
  header.writeUInt8(0, 11); // 过滤方式
  header.writeUInt8(0, 12); // 隔行扫描

  // 每行前面要加一个过滤字节（0 = 不过滤）
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);

  for (let row = 0; row < height; row += 1) {
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

/**
 * 画一个暖色圆点（与桌宠形象同一色系）。
 *
 * 边缘按距离做一次抗锯齿，避免在 16px 显示时出现锯齿。
 * 导出以便单测直接验证像素（中心不透明、四角透明）。
 */
export function renderTrayIconPixels(size: number): Buffer {
  const rgba = Buffer.alloc(size * size * 4);
  const center = (size - 1) / 2;
  const radius = size / 2 - 1;
  const [r, g, b] = [0xe0, 0xa2, 0x4e];

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const distance = Math.hypot(x - center, y - center);
      const coverage = Math.min(1, Math.max(0, radius - distance + 0.5));
      const offset = (y * size + x) * 4;

      rgba[offset] = r;
      rgba[offset + 1] = g;
      rgba[offset + 2] = b;
      rgba[offset + 3] = Math.round(coverage * 255);
    }
  }

  return rgba;
}

/** 托盘图标 PNG（32×32） */
export function createTrayIconPng(size = 32): Buffer {
  return encodePng(size, size, renderTrayIconPixels(size));
}
