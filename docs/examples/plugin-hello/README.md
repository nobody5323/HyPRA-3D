# 示例插件·问候（plugin-hello）

插件接口的最小可运行示例。它做三件事：

1. 从**声明式配置**里读问候语（用户在能力中心填，宿主自动渲染表单）；
2. 注册一个 function calling **工具** `hello_greet`，让模型可以调用；
3. 把自己的**调用次数**记在插件自己的数据目录 `data/plugins/plugin-hello/` 里。

接口说明见 [`docs/plugin-development.md`](../../plugin-development.md)。

## 怎么用

### 方式一：零拷贝（开发期推荐）

1. 在 `backend/.env` 里加上（把路径换成你放这个目录的位置）：

   ```ini
   PLUGIN_EXTRA_DIRS=<仓库路径>/docs/examples
   ```

   注意填的是**父目录**：宿主会把该目录下的每个子目录当作一个插件。
2. 重启后端 → 页面「能力中心 → 添加插件」→「重新扫描」。
3. 列表里出现「示例插件·问候」→ 展开详情 → **启用**。

### 方式二：导入

在「能力中心 → 添加插件」里填这个目录的路径（`.../docs/examples/plugin-hello`），
点「导入插件」——宿主会把它复制到 `backend/data/plugins/plugin-hello/`。

## 怎么验证

- 启用后打开详情，能看到 `greeting` / `signature` 两个字段（由 `settings_schema` 渲染）；
  改成别的问候语保存，下次调用即生效（保存配置会重跑 `build()`）。
- 在对话里说「打个招呼」，模型应调用 `hello_greet`；返回里带 `calls` 字段。
- 调用一次之后，`backend/data/plugins/plugin-hello/call-count.json` 会出现并递增。

## 这个示例演示了什么

| 位置 | 演示的点 |
| --- | --- |
| `manifest.json` 的 `layer` | 第三方插件必须写 `third-party` |
| `manifest.json` 的 `permissions` | 只读、不联网——本插件确实不需要读写外部路径 |
| `manifest.json` 的 `settings_schema` | 声明式配置：插件不写前端代码 |
| `build(ctx)` | 入口契约；会被重复调用，因此必须幂等 |
| `ToolSpec.description` | 这句话是写给**模型**看的，决定它会不会调用 |
| `ctx.own_data_dir()` | 插件状态目录，不需要额外声明写权限 |
| `ctx.logger` | 日志前缀 `plugin.plugin-hello`，排查时按前缀过滤 |

## 改成你自己的插件

1. 换 `manifest.json` 的 `id`（目录名与它一致）与 `display_name`；
2. 换 `plugin.py` 里的 `build()` 返回值——比如改成 `datasources` 贡献一份只读数据源；
3. 只保留你真正需要的权限声明（**未声明 = 不许**，缺省最严）。

需要看更完整的实现（真实数据解析、权限白名单、`datasource` 契约）时，读
`backend/plugins/tavern-bridge/plugin.py`。
