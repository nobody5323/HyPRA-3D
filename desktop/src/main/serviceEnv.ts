import fs from "node:fs/promises";
import path from "node:path";

/**
 * 首次启动时把 `.env` 模板复制到用户数据目录。
 *
 * 为什么需要：打包形态里没有 `backend/.env`（那是开发机上的本地文件，既不入库
 * 也不进包），而评审必须有个地方填 LLM / Embedding 的云端 key。后端在打包形态下
 * 会去 `<userData>/.env` 读配置（见 `backend/app/paths.py` 的 `config_file()`），
 * 所以这里把模板先送过去，用户改一个文件即可。
 *
 * 两条硬约束：
 * - **已存在就不覆盖**：用户填过的 key 不能被冲掉；
 * - **源不存在时静默跳过**：开发形态没有 `resources/`，这一步本就不该发生，
 *   更不能因此让程序起不来。
 */

/** 模板与目标的绝对路径（纯函数，便于测试） */
export interface UserEnvPaths {
  /** 源：随包分发的模板 */
  source: string;
  /** 目标：后端实际读取的用户配置 */
  target: string;
}

/** 由资源目录与用户数据目录推导模板/目标路径（纯函数，不做 IO） */
export function userEnvPaths(options: {
  resourcesPath: string;
  userDataDir: string;
}): UserEnvPaths {
  return {
    source: path.join(path.resolve(options.resourcesPath), "backend-res", ".env.example"),
    target: path.join(path.resolve(options.userDataDir), ".env"),
  };
}

/**
 * 确保用户配置存在；返回**本次新建**的文件路径，未新建（已存在/无模板）返回 null。
 */
export async function ensureUserEnvFile(options: {
  resourcesPath: string;
  userDataDir: string;
}): Promise<string | null> {
  const { source, target } = userEnvPaths(options);

  try {
    await fs.access(target);

    return null; // 已存在：这是用户的配置，不动它
  } catch {
    // 不存在 → 继续尝试复制
  }

  let template: string;

  try {
    template = await fs.readFile(source, "utf8");
  } catch {
    // 模板不存在：开发形态的正常情况（没有 resources/backend-res），静默跳过
    return null;
  }

  try {
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, template, "utf8");

    return target;
  } catch (error: unknown) {
    // 写不进去（权限/磁盘）：不能阻断启动，但要留下痕迹便于排查
    console.warn("[services] 无法生成用户配置模板", target, error);

    return null;
  }
}
