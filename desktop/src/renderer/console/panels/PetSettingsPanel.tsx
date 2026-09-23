import { MAX_PET_SCALE, MIN_PET_SCALE, PET_SCALE_STEP } from "../../../shared/ipc";
import type { ConsoleState } from "../../../shared/ipc";

/**
 * 桌宠设置分组。
 *
 * 与控制台可以并行操作：桌宠窗里的设置面板改的是**同一份设置**
 * （走主进程同一个 `commitSettings`），所以两边不会出现不一致的状态。
 */
export function PetSettingsPanel({
  state,
  bridge,
  onNotice,
}: {
  state: ConsoleState | null;
  bridge: typeof window.hyprConsole;
  /** 操作结果提示（由外层统一展示） */
  onNotice(text: string): void;
}) {
  const petState = state?.petState;
  const petSettings = state?.petSettings;
  const scale = petSettings?.scale ?? 1;

  const update = (patch: Parameters<NonNullable<typeof bridge>["updatePetSettings"]>[0], text: string) => {
    void bridge
      ?.updatePetSettings(patch)
      .then(() => onNotice(text))
      .catch((error: unknown) =>
        onNotice(`失败：${error instanceof Error ? error.message : String(error)}`),
      );
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-xl bg-surface-inset px-4 py-3 text-xs leading-relaxed text-ink-muted">
        这些改动会<strong className="text-ink">立即作用于桌宠窗</strong>
        （与桌宠窗内的设置面板共用同一份设置）。
        桌宠未启动时也可以先设好，启动时按这份设置生效。
      </p>

      <label className="flex max-w-md flex-col gap-1 text-xs text-ink-soft">
        <span>
          大小 <strong className="text-ink">{Math.round(scale * 100)}%</strong>
        </span>
        <input
          type="range"
          min={MIN_PET_SCALE}
          max={MAX_PET_SCALE}
          step={PET_SCALE_STEP}
          value={scale}
          onChange={(event) => update({ scale: Number(event.target.value) }, "桌宠大小已更新")}
          className="accent-accent"
        />
        <span className="text-[11px] text-ink-faint">
          范围 {MIN_PET_SCALE}–{MAX_PET_SCALE}，步进 {PET_SCALE_STEP}
        </span>
      </label>

      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={petSettings?.alwaysOnTop ?? false}
            onChange={(event) =>
              update({ alwaysOnTop: event.target.checked }, event.target.checked ? "已置顶" : "已取消置顶")
            }
            className="accent-accent"
          />
          窗口置顶
        </label>

        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={petSettings?.clickThrough ?? false}
            onChange={(event) =>
              update(
                { clickThrough: event.target.checked },
                event.target.checked
                  ? "已开启点击穿透（鼠标进入角色区域时可点「解锁交互」）"
                  : "已关闭点击穿透",
              )
            }
            className="accent-accent"
          />
          点击穿透
        </label>
      </div>

      <dl className="max-w-md rounded-xl bg-surface-inset px-4 py-3 text-[11px] text-ink-muted">
        <div className="flex justify-between border-b border-line py-1.5 last:border-0">
          <dt>桌宠窗</dt>
          <dd>{state?.petVisible ? "运行中（桌面上可见）" : "未启动（已收起到托盘）"}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-1.5 last:border-0">
          <dt>穿透状态</dt>
          <dd>
            {petState?.clickThrough ? "已开启" : "关闭"}
            {petState?.clickThrough && petState.controlInteractive ? "（光标正在角色上：可点击）" : ""}
          </dd>
        </div>
      </dl>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!bridge}
          onClick={() =>
            void bridge?.showPet().then(() => onNotice("桌宠已启动"))
          }
          className="rounded-full bg-accent-soft px-4 py-1.5 text-xs text-accent-text ring-1 ring-accent/30 disabled:opacity-40"
        >
          {state?.petVisible ? "聚焦桌宠" : "启动桌宠"}
        </button>
        <button
          type="button"
          disabled={!bridge || !state?.petVisible}
          onClick={() => void bridge?.hidePet().then(() => onNotice("桌宠已收起"))}
          className="rounded-full bg-surface-raised px-4 py-1.5 text-xs text-ink-muted ring-1 ring-line disabled:opacity-40"
        >
          收起桌宠
        </button>
      </div>

      <p className="text-[11px] leading-relaxed text-ink-faint">
        退出整个程序请用左侧底部的「退出 HyPRA」或托盘菜单——关闭控制台窗口只是把它收起来。
      </p>
    </div>
  );
}
