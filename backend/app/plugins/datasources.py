"""宿主侧的数据源聚合与「外部项 id」生成（`AGENTS.md §9.4` 的宿主侧半边）。

插件只产出**中性数据**（`DataSourceSnapshot`），把它变成宿主模型是宿主的事。
本模块提供所有映射器都要用的两件东西：

- `read_snapshots()`：读取**启用插件**贡献的数据源快照。用**鸭子类型**识别——
  能 `read()` 出 `DataSourceSnapshot` 的才算，`live2d-model-source` 这类 datasource
  产出的是别的东西。宿主因此既不 import 任何插件的类型，也不写死插件 id：
  以后多一个数据源插件，映射层自动多一份内容。
- `stable_item_id()`：外部项的宿主 id。

**id 为什么用哈希**：来源 id 里带中文与 `/`、`#`（如 `world/世界甲#1`），不能直接
当宿主 id；而「标题 slug」既会重复又会被改——id 一变，向量索引的键、记忆作用域、
工作台里的条目全都跟着漂移。哈希只依赖来源标识，重读多少次、顺序怎么变都一样。
"""

from __future__ import annotations

import hashlib
import logging

from app.plugins.contracts import DataSourceSnapshot

logger = logging.getLogger(__name__)


def stable_item_id(prefix: str, source: str, source_id: str) -> str:
    """外部项的宿主 id：`<prefix>-<12 位哈希>`。

    prefix 由各自的域自己定（世界书条目 / persona 各占一个命名空间）。
    """
    digest = hashlib.sha1(f"{source}|{source_id}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def read_snapshots() -> tuple[list[DataSourceSnapshot], list[str]]:
    """读取全部启用插件数据源的快照，返回 (快照列表, 警告)。

    单个数据源坏掉只记警告并跳过：一个坏源不该让用户丢掉其余全部设定
    （与 `MemoryStore` 的温层/冷层降级策略一致）。
    """
    from app.plugins.manager import get_plugin_manager  # noqa: PLC0415 - 避免模块级耦合

    try:
        sources = get_plugin_manager().registry.datasources()
    except Exception as exc:  # noqa: BLE001 - 插件不可用不应阻断对话
        return [], [f"插件数据源读取失败：{exc}"]

    snapshots: list[DataSourceSnapshot] = []
    warnings: list[str] = []
    for source in sources:
        read = getattr(source, "read", None)
        if not callable(read):
            continue
        try:
            snapshot = read()
        except Exception as exc:  # noqa: BLE001 - 同上：单个数据源不阻断其它
            warnings.append(f"数据源 {type(source).__name__} 读取失败：{exc}")
            continue
        if isinstance(snapshot, DataSourceSnapshot):
            snapshots.append(snapshot)
    return snapshots, warnings
