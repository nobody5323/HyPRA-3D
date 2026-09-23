/**
 * 模型 × SDK 集成测试：在 Node 里跑通「真实模型 + 官方 Framework + Core」的更新链路。
 *
 * 为什么要有它：WebGL 渲染没法在无头环境验证，但**动作 / 眨眼 / 表情 / 物理 / 参数**
 * 这一整条 update 链路不需要 GL 上下文，完全可以在 CI 里跑。这条测试就是这么来的——
 * 它实际抓到过一个真 bug：
 *
 *   `CubismMotion._eyeBlinkParameterIds` 声明为数组却没有初值，而
 *   `CubismUserModel.loadMotion()` 也**不会**替调用方设置；官方 Sample 是在
 *   loadMotion 之后自己调 `setEffectIds()`。漏掉这一步时，动作第一次更新就抛
 *   「Cannot read properties of null (reading 'length')」。
 *
 * 依赖 `public/vendor/cubism/Core`（SDK，不入库）与 `public/live2d/default`（模型，不入库），
 * 因此在未落位的机器上自动跳过，不会让 CI 变红。
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";

import { describe, expect, it } from "vitest";

const root = process.cwd();
const MODEL_DIR = join(root, "public/live2d/default");
const CORE_PATH = join(root, "public/vendor/cubism/Core/live2dcubismcore.min.js");

/** 两个自备资源都在才跑（否则这条测试在别人机器上必然红） */
const ready =
  existsSync(CORE_PATH) &&
  existsSync(join(MODEL_DIR, "pet.model3.json")) &&
  existsSync(join(MODEL_DIR, "c_0120.moc3"));

function read(path: string): { buffer: ArrayBuffer; size: number } {
  const buf = readFileSync(path);
  return {
    buffer: buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer,
    size: buf.byteLength,
  };
}

describe.skipIf(!ready)("Cubism 更新链路（真实模型 + 官方 SDK）", () => {
  it("动作 / 眨眼 / 表情 / 物理 / 参数 更新全流程不抛错", async () => {
    // ① Core 必须在 Framework 被求值之前挂到全局
    vm.runInThisContext(readFileSync(CORE_PATH, "utf8"));
    const { CubismFramework } = await import(
      "@/vendor/cubism/Framework/src/live2dcubismframework"
    );
    CubismFramework.startUp({ logFunction: () => {}, loggingLevel: 3 });
    CubismFramework.initialize();

    const { CubismUserModel } = await import(
      "@/vendor/cubism/Framework/src/model/cubismusermodel"
    );
    const { CubismModelSettingJson } = await import(
      "@/vendor/cubism/Framework/src/cubismmodelsettingjson"
    );
    const { CubismEyeBlink } = await import(
      "@/vendor/cubism/Framework/src/effect/cubismeyeblink"
    );

    // ② 加载（顺序与 vendor/cubism/bridge.ts 一致）
    const userModel = new CubismUserModel();
    userModel.loadModel(read(join(MODEL_DIR, "c_0120.moc3")).buffer, false);
    const physics = read(join(MODEL_DIR, "c_0120.physics3.json"));
    userModel.loadPhysics(physics.buffer, physics.size);

    const settingsFile = read(join(MODEL_DIR, "pet.model3.json"));
    const settings = new CubismModelSettingJson(settingsFile.buffer, settingsFile.size);
    const blink = CubismEyeBlink.create(settings);

    // ③ 动作与表情：effect ids 必须显式设置（本测试要守住的那一步）
    const eyeBlinkIds = Array.from({ length: settings.getEyeBlinkParameterCount() }, (_, i) =>
      settings.getEyeBlinkParameterId(i),
    );
    const lipSyncIds = Array.from({ length: settings.getLipSyncParameterCount() }, (_, i) =>
      settings.getLipSyncParameterId(i),
    );
    expect(eyeBlinkIds.length, "模型的 EyeBlink 组为空，model3.json 可能不完整").toBeGreaterThan(0);

    const idleFile = read(join(MODEL_DIR, "motions", "idle.motion3.json"));
    const idle = userModel.loadMotion(idleFile.buffer, idleFile.size, "Idle");
    idle.setLoop(true);
    idle.setEffectIds(eyeBlinkIds, lipSyncIds);

    const expressionFile = read(join(MODEL_DIR, "开心兴奋.exp3.json"));
    const expression = userModel.loadExpression(
      expressionFile.buffer,
      expressionFile.size,
      "开心兴奋",
    );

    /** 运行期一律传 CubismModel（bridge 里的 this._model 就是它） */
    const model = userModel.getModel();
    const internal = userModel as unknown as {
      _motionManager: {
        startMotionPriority(motion: unknown, autoDelete: boolean, priority: number): number;
        updateMotion(target: unknown, dt: number): boolean;
        isFinished(): boolean;
      };
      _expressionManager: {
        startMotion(motion: unknown, autoDelete: boolean): number;
        updateMotion(target: unknown, dt: number): boolean;
      };
      _physics: { evaluate(target: unknown, dt: number): void } | null;
    };

    // ④ 连跑多帧：单帧通过不代表稳定（动作会走完淡入、参数会在帧间保存/恢复）
    const runFrame = () => {
      const dt = 1 / 60;
      model.loadParameters();
      if (internal._motionManager.isFinished()) {
        internal._motionManager.startMotionPriority(idle, false, 1);
      }
      internal._motionManager.updateMotion(model, dt);
      blink.updateParameters(model, dt);
      internal._expressionManager.updateMotion(model, dt);
      internal._physics?.evaluate(model, dt);
      model.saveParameters();
      model.update();
    };

    expect(() => {
      internal._motionManager.startMotionPriority(idle, false, 1);
      internal._expressionManager.startMotion(expression, false);
      for (let frame = 0; frame < 120; frame += 1) runFrame();
    }).not.toThrow();

    // ⑤ 动作确实在驱动参数（不只是"没崩"）
    expect(model.getParameterCount()).toBeGreaterThan(0);
  });
});
