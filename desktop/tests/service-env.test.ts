import path from "node:path";

import { describe, expect, it } from "vitest";

import { userEnvPaths } from "../src/main/serviceEnv";

/**
 * 用户配置模板路径的测试。
 *
 * 只测纯函数部分（`ensureUserEnvFile` 要碰真实文件系统，那部分由打包后的
 * 首次启动手工验证）：这里要钉死的是「模板从哪来、落到哪去」——
 * 落错地方（比如落到只读的安装目录）会让用户永远填不上 key。
 */

describe("userEnvPaths", () => {
  it("模板取自随包资源，目标落在用户数据目录", () => {
    const paths = userEnvPaths({
      resourcesPath: "/install/resources",
      userDataDir: "/users/demo/AppData/Roaming/HyPRA",
    });

    expect(paths.source).toBe(
      path.join(path.resolve("/install/resources"), "backend-res", ".env.example"),
    );
    expect(paths.target).toBe(path.join(path.resolve("/users/demo/AppData/Roaming/HyPRA"), ".env"));
  });

  it("目标不是安装目录里的文件（否则用户改不了/写不进）", () => {
    const paths = userEnvPaths({
      resourcesPath: "/install/resources",
      userDataDir: "/users/demo/HyPRA",
    });

    expect(paths.target.startsWith(path.resolve("/install/resources"))).toBe(false);
  });

  it("传入相对路径时归一成绝对路径", () => {
    const paths = userEnvPaths({ resourcesPath: ".", userDataDir: "." });

    expect(path.isAbsolute(paths.source)).toBe(true);
    expect(path.isAbsolute(paths.target)).toBe(true);
  });
});
