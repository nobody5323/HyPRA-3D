import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

/**
 * 桌宠窗的本地静态资源服务器。
 *
 * **为什么桌面端要起一个 HTTP 服务，而不是直接 `file://` 加载页面：**
 * 前端（`frontend/public/`）里的资源引用全是**绝对路径**——`/live2d/<模型>/pet.model3.json`、
 * `/portraits/happy.svg`、`/vendor/cubism/Core/…`。在 `file://` 下这些会解析到磁盘根目录
 * （`file:///live2d/...`）而全部 404；挂一个 loopback 服务后与 Web 端完全同源，
 * 复用层一行都不用改。
 *
 * 端口**固定优先**（不是随机）：渲染层的 localStorage 按 origin 隔离，
 * 端口一变，登录/音色/会话指针这些本地偏好就全丢了。
 */

/** 首选端口：落在动态端口区间内，避开常见开发端口（3000/5173/8000） */
const DEFAULT_PREFERRED_PORT = 34567;

/** 端口被占用时的尝试次数（固定端口优先，占用则依次递增） */
const PORT_ATTEMPTS = 10;

/** 扩展名 → Content-Type。浏览器与 Cubism 运行时都依赖它，缺一项就是资源加载失败 */
const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
  ".ogg": "audio/ogg",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".map": "application/json; charset=utf-8",
  // Cubism 运行时用到的非标准扩展名（官方 SDK 以 fetch 读取，必须是正确类型）
  ".moc3": "application/octet-stream",
  ".frag": "text/plain; charset=utf-8",
  ".vert": "text/plain; charset=utf-8",
};

export interface AssetServer {
  /** 形如 `http://127.0.0.1:34567`（末尾无斜杠） */
  origin: string;
  /** 实际监听的端口 */
  port: number;
  close(): Promise<void>;
}

export interface StartAssetServerOptions {
  /** 静态资源根目录（构建产物目录） */
  root: string;
  /** 后端地址，用于生成 CSP 的 connect-src / media-src */
  backendOrigin?: string;
  /** 首选端口 */
  preferredPort?: number;
  /**
   * 额外的资源根：URL 前缀 → 磁盘目录。
   *
   * 用于用户**自备**的资源：发布包不含 Live2D 模型（授权与体积原因），
   * 用户把模型装到自己的数据目录后，`/live2d/...` 就从那里取。
   */
  extraRoots?: Record<string, string>;
}

/**
 * 把请求路径解析为根目录内的真实文件路径。
 *
 * 拒绝一切逃逸尝试（`..`、编码过的 `%2e%2e`、Windows 盘符、NUL 字节）。
 * 越界一律返回 undefined，由调用方回 404 —— 不抛错、不泄漏目录结构。
 */
export function resolveAssetPath(root: string, requestPath: string): string | undefined {
  let decoded: string;

  try {
    decoded = decodeURIComponent(requestPath);
  } catch {
    return undefined; // 非法的百分号编码
  }

  if (decoded.includes("\0") || /^[a-zA-Z]:/.test(decoded)) {
    return undefined;
  }

  // 统一分隔符后再逐段过滤：`a/../../b` 这类要在解析前就拦住
  const segments = decoded.split(/[\\/]+/).filter((segment) => segment.length > 0);

  if (segments.some((segment) => segment === "..")) {
    return undefined;
  }

  const relative = segments.join("/") || "index.html";
  const target = path.resolve(root, relative);
  const rootWithSeparator = path.resolve(root) + path.sep;

  // 双保险：即使前面漏了某种写法，最终路径也必须落在根目录内
  if (target !== path.resolve(root) && !target.startsWith(rootWithSeparator)) {
    return undefined;
  }

  return target;
}

/**
 * 魔珐星云 LiteSDK 的来源白名单。
 *
 * 数字人是**客户端渲染**：SDK 脚本从 CDN 拉取，会话与驱动数据再经网关
 * （HTTP 长轮询 → WebSocket 升级）往返。上面任何一环没放行，SDK 都建不起来
 * ——表现为「SDK 脚本加载失败」或初始化一直卡到超时。
 *
 * ⚠️ 这是桌宠窗安全边界上**唯一**允许的外部脚本来源，所以写成模块级常量、
 * 不随调用方参数变化：「这里放开了外部脚本」必须一眼看得见。
 */
/** SDK 脚本来源（**保持精确**：外部脚本来源越窄越好） */
const XMOV_SCRIPT_ORIGIN = "https://media.xingyun3d.com";
/**
 * 魔珐的**连接类**域名空间。
 *
 * 为什么用通配而不是逐个列：实测 SDK 会连一串子域，而**官方接入文档没写全**——
 * 会话 API 是 `nebula-agent.xingyun3d.com`，但真正跑驱动的 WebSocket 在
 * `ttsa-gateway-lite.xingyun3d.com`（这个域名文档里根本没有，是从 CSP 拦截报错里抓出来的）。
 * 逐个试错的代价是每一轮都要「改 CSP → 重建 → 重启桌面端 → 复现」。
 *
 * 通配的边界仍是**厂商级**的（只放开魔珐自己的域名空间），
 * 不等于放开整个 https；`script-src` 也没跟着放宽。
 */
const XMOV_ORIGIN_WILDCARD = "https://*.xingyun3d.com";
const XMOV_WS_WILDCARD = "wss://*.xingyun3d.com";
/**
 * 形象资源 CDN（阿里云 OSS）。
 *
 * **不给通配**：那是阿里云的域，不是魔珐的，通配等于敞开整个 OSS。
 * SDK 会 **fetch** 数字人数据（`char_data.bin.gz` 之类）而不是用 `<img>`，
 * 所以它必须出现在 `connect-src`，只写在 `img-src` 里不管用。
 */
const XMOV_ASSET_ORIGIN = "https://public-xmov.oss-cn-hangzhou.aliyuncs.com";

/** 生成页面用的 CSP（页面由本服务提供，因此用响应头而不是 meta 标签） */
export function buildContentSecurityPolicy(backendOrigin?: string): string {
  const backend = backendOrigin ? ` ${backendOrigin}` : "";

  return [
    "default-src 'self'",
    // 魔珐 SDK 脚本（数字人渲染）。除此之外不引入任何外部脚本
    `script-src 'self' ${XMOV_SCRIPT_ORIGIN}`,
    // 前端有内联 style（Live2D 的 canvas 尺寸、情绪光晕颜色）
    "style-src 'self' 'unsafe-inline'",
    // 魔珐 SDK 会从 CDN 取形象 / 贴图等资源
    `img-src 'self' data: blob:${backend} ${XMOV_SCRIPT_ORIGIN} ${XMOV_ORIGIN_WILDCARD} ${XMOV_ASSET_ORIGIN}`,
    `media-src 'self' data: blob:${backend} ${XMOV_SCRIPT_ORIGIN} ${XMOV_ORIGIN_WILDCARD} ${XMOV_ASSET_ORIGIN}`,
    // 连接：会话 API / 驱动 WebSocket / 形象数据 CDN
    `connect-src 'self'${backend} ${XMOV_ORIGIN_WILDCARD} ${XMOV_WS_WILDCARD} ${XMOV_ASSET_ORIGIN}`,
    // 魔珐 SDK 会从 **blob: URL 创建 Web Worker** 跑解码/渲染准备工作。
    // 不写这条就回落到 `script-src`（其中没有 blob:），Worker 被拦，
    // SDK 只报「浏览器能力检查失败」——而 WebGL / 编解码器其实都是好的（实测）。
    "worker-src 'self' blob:",
    "font-src 'self' data:",
    "object-src 'none'",
    "frame-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

function contentTypeOf(filePath: string): string {
  return MIME_TYPES[path.extname(filePath).toLowerCase()] ?? "application/octet-stream";
}

/**
 * 选择本次请求该用哪个资源根。
 *
 * 命中额外资源根时返回去掉前缀后的路径；否则用主根与原始路径。
 * 前缀按**最长优先**匹配，避免 `/live2d-extra` 被 `/live2d` 的规则抢走。
 */
export function resolveRootFor(
  options: { root: string; extraRoots: Record<string, string> },
  pathname: string,
): { root: string; pathname: string } {
  const prefixes = Object.keys(options.extraRoots).sort((a, b) => b.length - a.length);

  for (const prefix of prefixes) {
    const normalized = prefix.startsWith("/") ? prefix : `/${prefix}`;

    if (pathname === normalized || pathname.startsWith(`${normalized}/`)) {
      const directory = options.extraRoots[prefix];

      if (fs.existsSync(directory)) {
        return { root: directory, pathname: pathname.slice(normalized.length) || "/" };
      }
    }
  }

  return { root: options.root, pathname };
}

function respond(response: ServerResponse, status: number, body: string): void {
  response.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  response.end(body);
}

function handleRequest(
  request: IncomingMessage,
  response: ServerResponse,
  options: { root: string; backendOrigin?: string; extraRoots: Record<string, string> },
): void {
  if (request.method !== "GET" && request.method !== "HEAD") {
    respond(response, 405, "Method Not Allowed");

    return;
  }

  const requestUrl = new URL(request.url ?? "/", "http://127.0.0.1");
  // 额外资源根优先：用户自备模型的目录映射在构建产物目录之前
  const { root, pathname } = resolveRootFor(options, requestUrl.pathname);
  const filePath = resolveAssetPath(root, pathname);

  if (!filePath) {
    respond(response, 404, "Not Found");

    return;
  }

  let stat: fs.Stats;

  try {
    stat = fs.statSync(filePath);
  } catch {
    respond(response, 404, "Not Found");

    return;
  }

  if (!stat.isFile()) {
    respond(response, 404, "Not Found");

    return;
  }

  if (path.extname(filePath).toLowerCase() === ".html") {
    // CSP 只挂在页面上（静态资源不需要，挂了反而会随着每个请求重复传输）
    response.setHeader("Content-Security-Policy", buildContentSecurityPolicy(options.backendOrigin));
  }

  response.setHeader("Content-Type", contentTypeOf(filePath));
  response.setHeader("Content-Length", String(stat.size));
  // 本地资源随应用版本变化，允许缓存但不允许在窗口生命周期内失效
  response.setHeader("Cache-Control", "no-cache");

  if (request.method === "HEAD") {
    response.writeHead(200);
    response.end();

    return;
  }

  const stream = fs.createReadStream(filePath);

  stream.on("error", () => {
    if (!response.headersSent) {
      respond(response, 500, "Internal Server Error");
    } else {
      response.destroy();
    }
  });
  stream.pipe(response);
}

/** 在 loopback 上启动静态资源服务 */
export function startAssetServer(options: StartAssetServerOptions): Promise<AssetServer> {
  const root = path.resolve(options.root);
  const preferredPort = options.preferredPort ?? DEFAULT_PREFERRED_PORT;

  if (!fs.existsSync(root)) {
    return Promise.reject(new Error(`静态资源目录不存在：${root}`));
  }

  return new Promise<AssetServer>((resolve, reject) => {
    let attempt = 0;

    const extraRoots = options.extraRoots ?? {};

    const listen = (): void => {
      const port = preferredPort + attempt;
      const server = http.createServer((request, response) => {
        handleRequest(request, response, {
          root,
          backendOrigin: options.backendOrigin,
          extraRoots,
        });
      });

      server.once("error", (error: NodeJS.ErrnoException) => {
        if (error.code === "EADDRINUSE" && attempt < PORT_ATTEMPTS - 1) {
          attempt += 1;
          listen();

          return;
        }

        reject(error);
      });

      // 只绑 loopback：不监听外网地址，局域网内其它机器访问不到
      server.listen(port, "127.0.0.1", () => {
        resolve({
          origin: `http://127.0.0.1:${port}`,
          port,
          close: () =>
            new Promise<void>((done) => {
              server.close(() => done());
            }),
        });
      });
    };

    listen();
  });
}
