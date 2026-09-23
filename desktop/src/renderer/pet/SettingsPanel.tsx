import { MAX_PET_SCALE, MIN_PET_SCALE, PET_SCALE_STEP, type PetEnvironment, type PetWindowState } from "../../shared/ipc";
import type { BackendHealth } from "./useBackendHealth";

export interface SettingsPanelProps {
  state: PetWindowState;
  environment: PetEnvironment | null;
  health: BackendHealth;
  onUpdateScale(scale: number): void;
  onClose(): void;
}

/**
 * 桌宠设置面板。
 *
 * 只放桌宠窗口自身的开关（缩放 / 置顶 / 穿透）与运行环境信息；
 * 人设、模型、语音等**业务**设置仍在 Web 端界面里——桌面端不重复造一套配置界面。
 */
export function SettingsPanel({
  state,
  environment,
  health,
  onUpdateScale,
  onClose,
}: SettingsPanelProps) {
  return (
    <section className="petPanel" aria-label="桌宠设置">
      <header className="petPanelHeader">
        <span>桌宠设置</span>
        <button type="button" onClick={onClose} title="关闭（Esc）">
          关闭
        </button>
      </header>

      <div className="petPanelBody">
        <label className="petField">
          <span>
            大小 <strong>{Math.round(state.scale * 100)}%</strong>
          </span>
          <input
            type="range"
            min={MIN_PET_SCALE}
            max={MAX_PET_SCALE}
            step={PET_SCALE_STEP}
            value={state.scale}
            onChange={(event) => onUpdateScale(Number(event.target.value))}
          />
        </label>

        <p className="petHint">拖动桌宠可换位置；位置与大小都会被记住。</p>

        <dl className="petFacts">
          <dt>点击穿透</dt>
          <dd>{state.clickThrough ? "已开启（右键可关闭）" : "关闭"}</dd>
          <dt>窗口置顶</dt>
          <dd>{state.alwaysOnTop ? "是" : "否"}</dd>
          <dt>后端</dt>
          <dd>
            {environment?.apiBase ?? "—"}
            {health.online === null ? " · 探测中" : health.online ? " · 在线" : " · 未连接"}
          </dd>
          <dt>版本</dt>
          <dd>
            {environment ? `${environment.appVersion} · ${environment.platform}` : "—"}
            {environment?.development ? " · 开发模式" : ""}
          </dd>
        </dl>

        {health.online === false ? (
          <p className="petWarning">
            后端未连接：对话与播报不可用。请在项目根目录执行
            <code>docker compose up -d</code>，或本地启动 FastAPI 后再试。
          </p>
        ) : null}

        <p className="petHint">
          全局快捷键 <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> 显示 / 隐藏桌宠；
          托盘图标可唤出或退出。
        </p>
      </div>
    </section>
  );
}
