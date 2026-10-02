import type { AvatarRendererPreference, CredentialSource } from "@/lib/avatar/avatar-config";

/**
 * 桌宠形象来源的决策（纯函数，便于单测钉住）。
 *
 * 与 Web 端 `frontend/app/page.tsx` 的规则**基本一致**（同一个渲染方式偏好，
 * 两边不该给出不同结果），只有一处刻意不同：**`auto` 不自动套用部署配置的凭证**，
 * 理由见函数内注释。
 *
 * 为什么桌宠窗也能用 3D：魔珐 SDK 是**浏览器端本地渲染**，且数字人自带透明通道
 * （探针实测确认），与 `transparent: true` 的透明小窗天然契合。
 */

/** 形象来源：魔珐 3D（xmov）/ 本地渲染（Live2D → 静态立绘，由 `AvatarSurface` 再分档） */
export type PetRenderMode = "xmov" | "local";

export function resolvePetRenderMode(input: {
  /** 用户的渲染方式偏好 */
  renderer: AvatarRendererPreference;
  /** 凭证来源：`user`（界面填的，存后端）/ `env`（部署 `.env`）/ `none` */
  credentialsSource: CredentialSource;
  /** 当前这一版凭证是否已降级（SDK 建不起来 / 重连用尽） */
  degraded: boolean;
}): PetRenderMode {
  const { renderer, credentialsSource, degraded } = input;

  // `local` / `static`：**强制本地**——这正是「填了密钥也想看 Live2D」的场景。
  // 旧逻辑只看凭证有无，会让本地渲染器永远没机会跑（Web 端踩过这个坑）。
  if (renderer === "local" || renderer === "static") {
    return "local";
  }

  // 没凭证、或这一版凭证已经降级 → 本地渲染
  if (degraded || credentialsSource === "none") {
    return "local";
  }

  // `xmov`（强制魔珐）：连**部署配置**兜底的凭证也算——用户明确要求了
  if (renderer === "xmov") {
    return "xmov";
  }

  // `auto`：**只认界面填写的凭证**，不自动套用部署配置。
  //
  // 为什么与 Web 端不同：`backend/.env` 里那份是「部署默认」，通常是给 Web 端配的
  // **横屏**应用；桌宠的容器是竖屏 9:16，套用横屏应用会把人拉变形
  // （实测：同一个 appId 换比例就变形）。桌宠要 3D 就得有专门的竖屏应用，
  // 而「专门配了这一套」的信号就是**界面里填过**（source === "user"）。
  // 想用部署配置那一套（比如它本身就是竖屏），在设置面板里显式选「魔珐星云 SDK」。
  return credentialsSource === "user" ? "xmov" : "local";
}

/**
 * 传给本地渲染层（`AvatarSurface`）的渲染方式。
 *
 * 用户选了「魔珐」而它没起来（无凭证 / 降级）时，本地这一层只给**立绘**（`static`）——
 * 他选的是 3D，不该自动换成另一个模型渲染器（Live2D）去跑，那正是用户报的
 * 「会自动渲染 live2d」。其余偏好原样透传，由 `AvatarSurface` 自己分 Live2D / 立绘。
 */
export function resolveLocalRenderer(
  renderer: AvatarRendererPreference,
  xmovActive: boolean,
): AvatarRendererPreference {
  if (renderer === "xmov" && !xmovActive) {
    return "static";
  }

  return renderer;
}
