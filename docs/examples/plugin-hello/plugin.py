"""示例插件：演示 HyPRA 插件接口的最小可用形态。

这个文件刻意做三件事，正好覆盖写插件时最常用的三样东西：

1. **声明式配置**：从 `ctx.settings` 读用户在界面上填的值（`manifest.json` 的
   `settings_schema` 决定表单长什么样）；
2. **注册工具**：返回一个 `ToolSpec`，让模型可以自主调用它；
3. **保存自己的状态**：往 `ctx.own_data_dir()`（`data/plugins/plugin-hello/`）里记一笔——
   这个目录不需要额外声明权限，宿主已经把它划给本插件。

接口全貌见 `docs/plugin-development.md`；这里只 import `app.plugins.sdk`，
那是插件唯一稳定的入口（其余 `app.*` 都是宿主内部实现，随时可能变）。
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from app.plugins.sdk import PluginContext, ToolSpec

#: 计数文件名（放在插件自己的数据目录里）
_COUNTER_FILE = "call-count.json"


class GreetArgs(BaseModel):
    """工具参数（宿主用它校验模型给的 JSON，不合法会在进入 handler 前被挡下）。"""

    name: str = ""
    """要问候的人；留空则用当前对话里的用户称呼。"""


def _read_counter(ctx: PluginContext) -> int:
    """读调用次数（文件不存在 / 坏了都当 0——插件的状态损坏不该让它起不来）。"""
    path = ctx.own_data_dir() / _COUNTER_FILE
    if not path.is_file():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("count", 0))
    except Exception:  # noqa: BLE001 - 状态文件坏了就当没记过
        ctx.logger.warning("调用计数读取失败（按 0 处理）：%s", path)
        return 0


def _bump_counter(ctx: PluginContext) -> int:
    """把调用次数 +1 写回插件自己的数据目录，返回新值。

    注意这里是**直接文件 I/O** 而不是 `ctx.write_text()`：`own_data_dir()` 是宿主
    划给本插件的状态目录，本来就不需要（也不该）在 `manifest.permissions` 里声明写权限。
    读**外部**路径仍然必须走 `ctx.read_*`，否则权限声明会变成一句空话。
    """
    count = _read_counter(ctx) + 1
    path = ctx.own_data_dir() / _COUNTER_FILE
    path.write_text(json.dumps({"count": count}, ensure_ascii=False), encoding="utf-8")
    return count


def build(ctx: PluginContext) -> dict:
    """插件入口：宿主在 setup 阶段调用，返回本插件对宿主的贡献。

    `build()` 会被**重复调用**（启用 / 保存配置 / 重扫目录都会重跑），所以每次都在
    这里按最新的 `ctx.settings` 重算贡献——下面的 handler 直接闭包住本次的 `ctx`，
    配置改完保存就生效，不需要插件自己做缓存失效。
    """
    greeting = str(ctx.settings.get("greeting") or "").strip() or "你好"
    signature = str(ctx.settings.get("signature") or "").strip()

    def handler(args: GreetArgs, context) -> dict:
        """工具的执行体。

        `args` 是校验过的 `GreetArgs`；`context` 是宿主的 `ToolContext`
        （`companion_id` / `session_id` / `user_name` / `extras`）。
        这里抛异常不会中断对话：宿主会把错误信息当作结果回传给模型。
        """
        target_name = (args.name or "").strip() or getattr(context, "user_name", "") or "你"
        text = f"{greeting}，{target_name}！"
        if signature:
            text += f"——{signature}"
        return {
            "message": text,
            "calls": _bump_counter(ctx),
            "companion_id": getattr(context, "companion_id", ""),
        }

    ctx.logger.info("示例插件已就绪（问候语=%r，已调用 %d 次）", greeting, _read_counter(ctx))

    return {
        "tools": [
            ToolSpec(
                # 工具名会直接暴露给模型。真实插件建议加命名空间（如 `hello_greet`），
                # 免得与别的插件或 MCP 工具撞名——同名时后注册的会被前一个盖住。
                name="hello_greet",
                description=(
                    "用用户配置的问候语向对方问好。当用户说「打个招呼」「问好」"
                    "或想验证插件是否生效时使用。"
                ),
                parameters=GreetArgs.model_json_schema(),
                args_model=GreetArgs,
                handler=handler,
                tags=["demo", "plugin"],
            )
        ]
    }
