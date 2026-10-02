"""能力面与分层定义：插件能向宿主贡献什么。

设计依据见 `AGENTS.md §9`：
- 三层插件（core / builtin / third-party）经**同一注册表**暴露能力；
- 能力面（capability）描述"贡献什么"，与"必需性分层"是两个正交维度。

本模块只放定义，不含任何业务实现——保证插件框架不反向依赖业务代码。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PluginLayer(str, Enum):
    """插件必需性分层（`AGENTS.md §9.2`）。

    core     换掉它产品就不成立（策略类：提示词组装、记忆门面、编排、情绪链路…），不可禁用；
    builtin  随项目分发、默认启用，但可替换或禁用（实现类：provider / 存储后端 / 工具…）；
    third_party  用户安装的第三方插件。
    """

    CORE = "core"
    BUILTIN = "builtin"
    THIRD_PARTY = "third-party"


class CapabilityType(str, Enum):
    """能力面（`AGENTS.md §9.4`）。"""

    PROVIDER = "provider"
    """按接口提供某类能力的实现（LLM / 数字人 / 记忆后端…），由宿主工厂按名选中。"""

    TOOL = "tool"
    """注册 function calling 工具（`ToolSpec`），供模型自主调用。"""

    DATASOURCE = "datasource"
    """读取外部数据源，产出**中性结构化对象**。

    硬约定：插件只负责"读 + 产出中性数据"，映射到宿主模型（WorldBookEntry / persona…）
    一律由宿主完成——保证多数据源共享同一套模型（`AGENTS.md §9.4`）。
    """

    PROMPT = "prompt"
    """往指定注入点贡献提示词片段。"""

    HOOK = "hook"
    """订阅对话生命周期事件（回复后、记忆写入前…）。"""

    SETTINGS = "settings"
    """声明式配置（JSON Schema → 宿主渲染表单），插件不自带前端组件。"""


class FilesystemPermission(BaseModel):
    """文件系统权限。"""

    read: list[str] = Field(
        default_factory=list,
        description="允许读取的路径白名单（支持 ${setting_name} 占位符，运行时由配置值替换）",
    )
    write: bool = Field(
        default=False,
        description="是否允许写文件系统；默认 False（只读插件）",
    )
    write_paths: list[str] = Field(
        default_factory=list,
        description="允许写入的路径白名单；仅当 write=True 时生效",
    )


class NetworkPermission(BaseModel):
    """网络权限。"""

    hosts: list[str] = Field(
        default_factory=list,
        description="允许访问的主机白名单；空表示不访问网络",
    )


class Permission(BaseModel):
    """插件的权限声明（嵌套结构，与 `AGENTS.md §9.3` 的 manifest 示例一致）。

    **这是架构约束，不是口头承诺**：宿主在把能力交给插件前强制校验本声明，
    未声明的资源插件拿不到句柄（见 `AGENTS.md §9.3`）。
    典型用例：酒馆数据接入插件声明 `filesystem.write=False`，宿主因此
    **无法**给它写句柄——“绝不修改用户酒馆数据”由类型系统而非约定保证。

    JSON 形态::

        "permissions": { "filesystem": { "read": ["${tavern_data_dir}"], "write": false } }
    """

    filesystem: FilesystemPermission = Field(
        default_factory=FilesystemPermission, description="文件系统权限（默认只读）"
    )
    network: NetworkPermission = Field(
        default_factory=NetworkPermission, description="网络权限（默认为空，不联网）"
    )

    @property
    def is_read_only(self) -> bool:
        """是否为只读插件（不写任何文件）。"""
        return not self.filesystem.write

    def check_consistent(self) -> None:
        """校验跨字段不变量：只读插件不得声明写路径。

        收在这里做**单一事实来源**的理由：这是宿主自己的硬约束，而校验它的地方有两个
        ——注册表（拒绝注册）与插件写入器（拒绝落盘）。两处各写一份判断，就会出现
        「一个放行了、另一个拒收」的组合：写入器放行 → manifest 已落盘 →
        发现阶段注册失败——最坏的形态是**安装接口 500 且后端下次启动直接报错**。
        （真实发生过一次，见 `tests/test_plugins/test_writer.py` 的相应用例。）

        抛出:
            ValueError: 声明自相矛盾（调用方转成自己的错误类型/状态码）。
        """
        if self.is_read_only and self.filesystem.write_paths:
            raise ValueError(
                "声明了写路径但 filesystem.write=false：只读插件的写声明无效"
                "（AGENTS.md §9.3）"
            )


class PluginState(str, Enum):
    """插件在宿主中的运行状态（供管理 UI 展示）。"""

    DISCOVERED = "discovered"   # 已发现，未加载
    LOADED = "loaded"           # 已 setup，未 start
    STARTED = "started"         # 运行中
    DISABLED = "disabled"       # 被用户禁用
    FAILED = "failed"           # 加载或启动失败（记录原因，不阻断宿主）
