"""世界书挂载清单的**唯一**读取入口（酒馆模式下的「这个角色知道哪几本书」）。

为什么单独一个模块：挂载清单需要被**三个位置**用到，而它们分属不同层——

- 检索链路（`app/graph/nodes.py` → `KnowledgeRetriever.retrieve`）：决定查哪几本；
- 管理 API（`app/api/plugins.py`）：读/写清单，供界面勾选；
- 同步/状态接口：把「可选来源」与「已挂载来源」对应上，供界面渲染。

三处各写一套读法的话，「界面显示挂着的」与「实际检索的」会漂移——那种 bug 表现为
「我明明挂了这本，它却答不上来」，且极难定位。所以清单的读取只走这里。

**存储位置**：`tavern-bridge` 插件配置里的 `tavern_mounted_books`（与
`tavern_disabled_books` 同处，管理一致）。用**来源标识**（`world/<文件名>` /
`char/<角色名>`）而非作用域键——作用域键是派生值（哈希），写进配置既不直观、
也会在哈希算法变更时静默失效；来源标识是人可读、稳定的。

**默认全不挂**：配置缺失/为空 → 返回空列表 → 河馆模式一本世界书都不查。
这是刻意的（`AGENTS.md §8.2`）：不想让没用到的世界书被串味召回。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 插件配置里的挂载清单键名（与 `plugin.DISABLED_BOOKS_KEY` 同一片配置）
MOUNTED_BOOKS_KEY = "tavern_mounted_books"

#: 贡献酒馆数据源的插件 id（清单就存在它的配置里）
PLUGIN_ID = "tavern-bridge"


def load_mounted_books() -> list[str]:
    """读取当前挂载的世界书来源标识；任何异常都降级为「一本都不挂」。

    降级方向刻意选「空」而不是「全部」：清单读不出来时，宁可这一轮查不到世界书，
    也不要突然把所有书都灌进去——后者会让角色说出完全无关作品的内容，
    用户会以为记忆坏了。而「空」只是少给点知识，对话仍然正常。
    """
    try:
        from app.plugins.manager import get_plugin_manager  # noqa: PLC0415

        raw = get_plugin_manager()._load_settings(PLUGIN_ID).get(  # noqa: SLF001
            MOUNTED_BOOKS_KEY
        )
    except Exception as exc:  # noqa: BLE001 - 读不到就当没挂
        logger.warning("读取世界书挂载清单失败（按未挂载处理）：%s", exc)
        return []

    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def save_mounted_books(sources: list[str]) -> list[str]:
    """写入挂载清单（读插件原有配置 → 只改这一个键 → 写回）。返回规范化后的清单。

    只改这一个键：插件配置里还有 `tavern_dir`、各层预算等，整体覆盖会冲掉它们。
    """
    from app.plugins.manager import get_plugin_manager  # noqa: PLC0415

    manager = get_plugin_manager()
    settings = dict(manager._load_settings(PLUGIN_ID))  # noqa: SLF001
    cleaned: list[str] = []
    for item in sources:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    settings[MOUNTED_BOOKS_KEY] = cleaned
    manager.save_settings(PLUGIN_ID, settings)
    return cleaned
