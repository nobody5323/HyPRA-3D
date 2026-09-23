/**
 * Cubism 运行时桥：把官方 Cubism Web SDK 的 API 收敛成渲染器真正需要的一小撮能力。
 *
 * 为什么要这层桥（而不是让渲染器直接 import 官方源码）：
 * 1. **未安装 SDK 也能 build**——渲染器只依赖本文件的接口，实现在构建期由
 *    `@cubism-bridge` 的双候选 alias 决定（见 tsconfig.json / next.config.mjs）；
 * 2. **可测试**——渲染器的全部逻辑都能用假桥（fake bridge）验证，不需要 SDK、
 *    模型或 WebGL，这在 CI 与评审环境里是唯一可行的验证方式；
 * 3. **换 SDK 版本只改桥**——渲染器里的参数映射、表情切换、状态机不受影响。
 *
 * 真实实现在 `vendor/cubism/bridge.ts`（由 `scripts/setup-cubism.mjs` 从官方 SDK 落位）；
 * 未安装时由 `cubism-bridge.unavailable.ts` 兜底。
 */

/** 加载模型所需的上下文 */
export interface CubismLoadOptions {
  /** 渲染目标画布（桥内部负责取 WebGL 上下文、建 renderer、装贴图） */
  canvas: HTMLCanvasElement;
  /** 模型入口 URL，如 `/live2d/default/pet.model3.json` */
  modelUrl: string;
  /** shader 目录（官方 SDK 的 shader 由运行时 fetch 加载，不进打包） */
  shaderBaseUrl: string;
  /** SDK 自身日志（诊断用） */
  onLog?: (message: string) => void;
}

/**
 * 一个已加载模型的句柄。
 *
 * 刻意只暴露「设参数 / 播表情 / 播动作 / 每帧更新」四类能力：
 * 变形、物理、混合、WebGL 绘制全部由官方 SDK 内部完成，本项目不重复造轮子。
 */
export interface CubismModelHandle {
  /**
   * 设置参数值。
   *
   * @returns 模型是否存在该参数（不存在时返回 false，且**不抛错**——
   *          不同模型的参数集不同，缺参数是常态而非异常）
   */
  setParameter(name: string, value: number): boolean;
  /** 读取参数当前值（不存在时返回 0） */
  getParameter(name: string): number;
  /** 播放模型自带表情（`exp3.json`）；返回是否找到该表情 */
  playExpression(name: string): boolean;
  /**
   * 收回当前表情，让被表情改过的参数回到模型默认值。
   *
   * 为什么必须有这个方法：表情是**叠加**在参数上的（exp3.json 里的
   * `Blend: "Add"`），停掉表情动作并不会自动把参数还原。
   * 没有它就会出现「情绪回到中性后，上一张脸还挂在模型上」的残留。
   */
  resetExpression(): void;
  /** 播放指定动作（按组名，如 `Idle`）；返回是否找到 */
  playMotion(group: string, index: number, loop: boolean): boolean;
  /** 每帧推进：更新动作/物理并绘制 */
  update(deltaSeconds: number): void;
  /** 画布尺寸随窗口/DPR 变化时重新同步（含投影矩阵） */
  resize(): void;
  /** 模型实际拥有的参数名（用于映射校验与诊断） */
  parameterNames(): readonly string[];
  /** 释放该模型占用的资源 */
  release(): void;
}

/** Cubism 运行时桥 */
export interface CubismBridge {
  /**
   * SDK 是否可用。
   *
   * 未安装 SDK 时为 `false`（同步常量），调用方据此**立即**降级，
   * 不必等待任何异步探测。
   */
  readonly available: boolean;
  /** 不可用时的原因（展示给用户，说明要做什么） */
  readonly unavailableReason: string;
  /** 初始化框架（幂等，可重复调用） */
  init(): void;
  /** 加载模型 */
  load(options: CubismLoadOptions): Promise<CubismModelHandle>;
}

/** 官方 SDK 的默认落位路径（与 setup 脚本、.gitignore 保持一致） */
export const CUBISM_SHADER_BASE_URL = "/vendor/cubism/Shaders/WebGL/";
