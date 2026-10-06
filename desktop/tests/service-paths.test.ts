import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  applyOverrides,
  derivePackagedPaths,
  deriveServicePaths,
  describeMissingFrontendPath,
  describeMissingPath,
  findProjectRoot,
  isProjectRoot,
  resolveServicePaths,
} from "../src/main/servicePaths";

/**
 * 服务路径解析测试。
 *
 * 全部用注入的 `exists`，不碰真实文件系统：这样测的是**判定逻辑**本身
 * （找到哪个目录算项目根、覆盖项怎么合并），不受运行环境上装没装东西影响。
 */

/** 造一个「只有这些路径存在」的判定函数 */
function fakeExists(present: string[]): (target: string) => boolean {
  const normalized = new Set(present.map((entry) => path.resolve(entry)));

  return (target) => normalized.has(path.resolve(target));
}

const ROOT = path.resolve("/repo");

describe("deriveServicePaths", () => {
  it("由项目根推导出 Python、Qdrant 与后端目录", () => {
    const paths = deriveServicePaths(ROOT);

    expect(paths.projectRoot).toBe(ROOT);
    expect(paths.pythonExe).toBe(path.join(ROOT, ".venv", "Scripts", "python.exe"));
    expect(paths.qdrantExe).toBe(path.join(ROOT, "_local", "qdrant", "qdrant.exe"));
    expect(paths.qdrantDir).toBe(path.join(ROOT, "_local", "qdrant"));
    expect(paths.backendDir).toBe(path.join(ROOT, "backend"));
    expect(paths.frontendDir).toBe(path.join(ROOT, "frontend"));
  });

  it("标记为开发形态（packaged=false）", () => {
    expect(deriveServicePaths(ROOT).packaged).toBe(false);
  });

  it("传入相对路径时归一成绝对路径", () => {
    expect(path.isAbsolute(deriveServicePaths(".").projectRoot)).toBe(true);
  });
});

describe("derivePackagedPaths", () => {
  const RESOURCES = path.resolve("/install/resources");
  const USER_DATA = path.resolve("/users/demo/AppData/Roaming/HyPRA");

  it("后端与 Qdrant 都取自随包资源（不再需要 Python 解释器）", () => {
    const paths = derivePackagedPaths({ resourcesPath: RESOURCES, userDataDir: USER_DATA });

    expect(paths.packaged).toBe(true);
    expect(paths.projectRoot).toBe(RESOURCES);
    expect(paths.pythonExe).toBe(path.join(RESOURCES, "backend", "backend.exe"));
    expect(paths.qdrantExe).toBe(path.join(RESOURCES, "qdrant", "qdrant.exe"));
    expect(paths.backendDir).toBe(path.join(RESOURCES, "backend"));
    // Web 端现在是**随包分发**的静态产物（由主进程内的 assetServer 托管），
    // 目录名是 web 而不是 frontend —— 后者是「源码 + node_modules」那个 Node 项目
    expect(paths.frontendDir).toBe(path.join(RESOURCES, "web"));
  });

  it("Qdrant 数据目录落在用户数据目录下，而不是只读的 resources 里", () => {
    const paths = derivePackagedPaths({ resourcesPath: RESOURCES, userDataDir: USER_DATA });

    expect(paths.qdrantDir).toBe(path.join(USER_DATA, "qdrant"));
    // 关键断言：安装目录（通常是 Program Files）普通用户写不进去
    expect(paths.qdrantDir.startsWith(path.resolve(RESOURCES))).toBe(false);
  });
});

describe("applyOverrides", () => {
  it("只覆盖填了的项", () => {
    const base = deriveServicePaths(ROOT);
    const next = applyOverrides(base, { pythonPath: "D:/py/python.exe" });

    expect(next.pythonExe).toBe(path.resolve("D:/py/python.exe"));
    expect(next.qdrantExe).toBe(base.qdrantExe);
  });

  it("Qdrant 工作目录跟着可执行文件走（数据不该落在旧目录）", () => {
    const next = applyOverrides(deriveServicePaths(ROOT), { qdrantPath: "E:/tools/qdrant.exe" });

    expect(next.qdrantDir).toBe(path.dirname(path.resolve("E:/tools/qdrant.exe")));
  });

  it("空串表示不覆盖", () => {
    const base = deriveServicePaths(ROOT);

    expect(applyOverrides(base, { pythonPath: "", qdrantPath: "" })).toEqual(base);
  });
});

describe("isProjectRoot / findProjectRoot", () => {
  it("两样必需的东西都在才算项目根（只看 .venv 会把别的项目误判成本项目）", () => {
    const paths = deriveServicePaths(ROOT);

    expect(isProjectRoot(ROOT, fakeExists([paths.pythonExe]))).toBe(false);
    expect(isProjectRoot(ROOT, fakeExists([paths.pythonExe, paths.qdrantExe]))).toBe(true);
  });

  it("从深层目录逐级向上找到项目根", () => {
    const paths = deriveServicePaths(ROOT);
    const deep = path.join(ROOT, "desktop", "dist", "main");

    expect(findProjectRoot([deep], fakeExists([paths.pythonExe, paths.qdrantExe]))).toBe(ROOT);
  });

  it("向上找到盘根也没找到时返回 null", () => {
    expect(findProjectRoot([path.join(ROOT, "a", "b")], fakeExists([]))).toBeNull();
  });

  it("按顺序尝试多个起始目录（dev 的 __dirname 与安装目录都试一遍）", () => {
    const paths = deriveServicePaths(ROOT);
    const other = path.resolve("/elsewhere/deep");

    expect(findProjectRoot([other, path.join(ROOT, "desktop")], fakeExists([paths.pythonExe, paths.qdrantExe]))).toBe(
      ROOT,
    );
  });
});

describe("resolveServicePaths", () => {
  it("用户填了项目根就以它为准（哪怕探测能找到一个）", () => {
    const found = deriveServicePaths(ROOT);
    const explicit = path.resolve("/custom/root");
    const paths = resolveServicePaths({
      searchFrom: [path.join(ROOT, "desktop")],
      overrides: { projectRoot: explicit },
      exists: fakeExists([found.pythonExe, found.qdrantExe]),
    });

    expect(paths?.projectRoot).toBe(explicit);
  });

  it("没找到项目根时返回 null（代管不成立，由界面给出可读说明）", () => {
    expect(
      resolveServicePaths({ searchFrom: [ROOT], overrides: {}, exists: fakeExists([]) }),
    ).toBeNull();
  });

  it("路径覆盖叠加在探测结果上", () => {
    const paths = deriveServicePaths(ROOT);
    const resolved = resolveServicePaths({
      searchFrom: [ROOT],
      overrides: { qdrantPath: "E:/tools/qdrant.exe" },
      exists: fakeExists([paths.pythonExe, paths.qdrantExe]),
    });

    expect(resolved?.pythonExe).toBe(paths.pythonExe);
    expect(resolved?.qdrantExe).toBe(path.resolve("E:/tools/qdrant.exe"));
  });
});

describe("describeMissingPath", () => {
  it("缺什么就报什么（界面直接把这句话显示出来）", () => {
    const paths = deriveServicePaths(ROOT);

    expect(describeMissingPath(paths, fakeExists([]))).toContain("找不到 Python 解释器");
    expect(describeMissingPath(paths, fakeExists([paths.pythonExe]))).toContain("找不到 Qdrant");
    expect(
      describeMissingPath(paths, fakeExists([paths.pythonExe, paths.qdrantExe])),
    ).toContain("找不到后端目录");
  });

  it("都齐了返回 null", () => {
    const paths = deriveServicePaths(ROOT);
    const exists = fakeExists([paths.pythonExe, paths.qdrantExe, paths.backendDir]);

    expect(describeMissingPath(paths, exists)).toBeNull();
  });

  it("打包形态缺的是 exe，报错文案不该再提 Python", () => {
    const paths = derivePackagedPaths({
      resourcesPath: path.resolve("/install/resources"),
      userDataDir: path.resolve("/users/demo/HyPRA"),
    });
    const message = describeMissingPath(paths, fakeExists([]));

    expect(message).toContain("找不到后端可执行文件");
    expect(message).not.toContain("Python 解释器");
  });
});

describe("describeMissingFrontendPath", () => {
  const paths = deriveServicePaths(ROOT);
  const nodeModules = path.join(paths.frontendDir, "node_modules");

  it("判据是「前端目录 + 它自己的依赖」，与 Python / Qdrant 一概无关", () => {
    // 备齐了后端那两样，对前端仍然一文不值
    expect(describeMissingFrontendPath(paths, fakeExists([paths.pythonExe, paths.qdrantExe]))).toContain(
      "找不到 Web 前端目录",
    );
    expect(describeMissingFrontendPath(paths, fakeExists([paths.frontendDir]))).toContain("npm install");
    expect(describeMissingFrontendPath(paths, fakeExists([paths.frontendDir, nodeModules]))).toBeNull();
  });

  it("缺依赖时报的是 npm install，不是一句难懂的模块解析错误", () => {
    const message = describeMissingFrontendPath(paths, fakeExists([paths.frontendDir]));

    expect(message).toContain("node_modules");
    expect(message).toContain("npm install");
    expect(message).not.toContain("Python");
  });

  it("打包形态缺资源时报「安装包不完整」，而不是让人去 npm install", () => {
    const packaged = derivePackagedPaths({
      resourcesPath: path.resolve("/install/resources"),
      userDataDir: path.resolve("/users/demo/HyPRA"),
    });
    const message = describeMissingFrontendPath(packaged, fakeExists([]));

    // 打包形态没有 node_modules 这个概念，责任在安装包而不在用户
    expect(message).toContain("安装包可能不完整");
    expect(message).not.toContain("npm install");
  });

  it("打包形态下 Web 端资源齐备（含 index.html）时判为可用", () => {
    const packaged = derivePackagedPaths({
      resourcesPath: path.resolve("/install/resources"),
      userDataDir: path.resolve("/users/demo/HyPRA"),
    });

    // 只有目录、没有 index.html 仍算不可用（入口缺失）
    expect(
      describeMissingFrontendPath(packaged, fakeExists([packaged.frontendDir])),
    ).toContain("index.html");

    // 目录与入口都在 → 通过
    expect(
      describeMissingFrontendPath(
        packaged,
        fakeExists([packaged.frontendDir, path.join(packaged.frontendDir, "index.html")]),
      ),
    ).toBeNull();
  });
});

describe("resolveServicePaths（打包形态）", () => {
  const RESOURCES = path.resolve("/install/resources");
  const USER_DATA = path.resolve("/users/demo/AppData/Roaming/HyPRA");
  const packaged = { resourcesPath: RESOURCES, userDataDir: USER_DATA };

  it("传了 packaged 就用随包路径，不再去探测项目根", () => {
    const devPaths = deriveServicePaths(ROOT);
    const resolved = resolveServicePaths({
      searchFrom: [ROOT],
      overrides: {},
      // 探测本来能命中项目根，但打包形态不该走探测
      exists: fakeExists([devPaths.pythonExe, devPaths.qdrantExe]),
      packaged,
    });

    expect(resolved?.packaged).toBe(true);
    expect(resolved?.projectRoot).toBe(RESOURCES);
    expect(resolved?.pythonExe).toBe(path.join(RESOURCES, "backend", "backend.exe"));
  });

  it("packaged 为 null 时与省略该参数完全一致（回归保护）", () => {
    const paths = deriveServicePaths(ROOT);
    const exists = fakeExists([paths.pythonExe, paths.qdrantExe]);
    const withNull = resolveServicePaths({
      searchFrom: [ROOT],
      overrides: {},
      exists,
      packaged: null,
    });
    const omitted = resolveServicePaths({ searchFrom: [ROOT], overrides: {}, exists });

    expect(withNull).toEqual(omitted);
    expect(withNull?.packaged).toBe(false);
  });

  it("打包形态下用户的路径覆盖依然生效", () => {
    const resolved = resolveServicePaths({
      searchFrom: [],
      overrides: { qdrantPath: "E:/tools/qdrant.exe" },
      exists: fakeExists([]),
      packaged,
    });

    expect(resolved?.qdrantExe).toBe(path.resolve("E:/tools/qdrant.exe"));
  });
});
