import { describe, expect, it } from "vitest";

import { DEFAULT_SERVICE_SETTINGS, normalizeServiceSettings } from "../src/shared/ipc";

/**
 * 服务设置归一化测试。
 *
 * 这些字段决定「进程怎么被启动」：路径写歪会去起别的目录，布尔值写歪会悄悄不启动服务。
 * 设置文件是用户可手改的，所以每一项都要能扛住乱写。
 */
describe("normalizeServiceSettings", () => {
  it("缺省时自动启动开启、reload 关闭（这两个默认值就是「一键启动」的含义）", () => {
    expect(normalizeServiceSettings(undefined)).toEqual(DEFAULT_SERVICE_SETTINGS);
    expect(normalizeServiceSettings({})).toEqual(DEFAULT_SERVICE_SETTINGS);
    expect(DEFAULT_SERVICE_SETTINGS.autoStart).toBe(true);
    expect(DEFAULT_SERVICE_SETTINGS.backendReload).toBe(false);
  });

  it("布尔字段只认真布尔值（写个 \"yes\" 不该把自动启动关掉）", () => {
    expect(normalizeServiceSettings({ autoStart: "yes" }).autoStart).toBe(true);
    expect(normalizeServiceSettings({ autoStart: 1 }).autoStart).toBe(true);
    expect(normalizeServiceSettings({ autoStart: null }).autoStart).toBe(true);
    expect(normalizeServiceSettings({ autoStart: false }).autoStart).toBe(false);

    expect(normalizeServiceSettings({ backendReload: "true" }).backendReload).toBe(false);
    expect(normalizeServiceSettings({ backendReload: true }).backendReload).toBe(true);
  });

  it("路径字段去首尾空白，非字符串当没填", () => {
    const settings = normalizeServiceSettings({
      projectRoot: "  C:/repo  ",
      pythonPath: 42,
      qdrantPath: null,
    });

    expect(settings.projectRoot).toBe("C:/repo");
    expect(settings.pythonPath).toBe("");
    expect(settings.qdrantPath).toBe("");
  });

  it("只保留已知字段（设置文件会被下次启动读回）", () => {
    expect(normalizeServiceSettings({ autoStart: true, evil: "x", projectRoot: "/a" })).toEqual({
      ...DEFAULT_SERVICE_SETTINGS,
      projectRoot: "/a",
    });
  });
});
