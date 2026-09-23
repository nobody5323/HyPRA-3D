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

/** 生成页面用的 CSP（页面由本服务提供，因此用响应头而不是 meta 标签） */export function buildContentSecurityPolicy(backendOrigin?: string): string {
  const backend = backendOrigin ? ` ${backendOrigin}` : "";

  return [
    "default-src 'self'",
    "script-src 'self'",
    // 前端有内联 style（Live2D 的 canvas 尺寸、情绪光晕颜色）
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob:${backend}`,
    `media-src 'self' data: blob:${backend}`,
    `connect-src 'self'${backend}`,
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
