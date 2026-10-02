# 插件开发指南（Plugin Development）

> 本文是**插件接口的公开说明**：写插件的人只需要读这一篇。
> 设计背景与取舍见 [`AGENTS.md §9`](../AGENTS.md)（插件体系）与
> [`docs/plugin-market.md`](plugin-market.md)（未来的市场机制，尚未实现）。
>
> 可复制的最小示例：[`docs/examples/plugin-hello/`](examples/plugin-hello/)。
> 公开接口面（import 目标）：`app/plugins/sdk.py`。

## 0. 30 秒上手

一个插件 = **一个目录**，里面两个文件：

```
my-plugin/
  manifest.json      # 声明：叫什么、贡献什么能力、要什么权限、配置长什么样
  plugin.py          # 入口：必须有 build(ctx)，返回对宿主的贡献
```

`plugin.py` 最小形态：

```python
from app.plugins.sdk import PluginContext


def build(ctx: PluginContext) -> dict:
    ctx.logger.info("我的插件已就绪")
    return {"tools": []}
```

接入程序（二选一，见 [§8](#8-怎么把插件接进程序)）：

1. **零拷贝（开发期推荐）**：把这个目录的**父目录**填进 `.env` 的 `PLUGIN_EXTRA_DIRS`，
   重启后端，然后在界面「能力中心 → 添加插件」点「重新扫描」；
2. **导入**：在同一个面板里填这个目录的路径，点「导入插件」（复制进用户插件目录）。

出现之后，在能力中心里把它**启用**——插件的代码只有在启用后才会被导入执行。

## 1. 目录与文件

| 文件 / 目录 | 必需 | 说明 |
| --- | --- | --- |
| `manifest.json` | ✅ | 插件声明，见 §2 |
| 入口文件（默认 `plugin.py`） | ✅ | `manifest.entry` 指向它，里面必须有 `build(ctx)` |
| 其它 `.py` | ❌ | 可以拆模块，入口里正常 `import` 同级文件（宿主按文件路径导入，不要求是包） |
| `README.md` / 许可证 | ❌ | 建议有：来源与许可是用户判断要不要装它的依据 |
| `__pycache__` / `.git` / `.venv` | — | 导入时会被跳过，不会被复制进用户插件目录 |

插件代码**随宿主进程运行**，因此可以直接 `import` 宿主提供的模块——
但请只 import `app.plugins.sdk`（稳定面），其余 `app.*` 视为内部实现，随时可能变。

## 2. `manifest.json` 字段

字段定义在 `app/plugins/manifest.py: PluginManifest`。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` | string | — | **必需**。唯一标识：小写字母/数字/`.`/`_`/`-`，≤64 字符，首字符为字母或数字。目录名建议与它一致 |
| `display_name` | string | — | **必需**。界面上显示的名字 |
| `version` | string | `0.0.0` | 语义化版本（自己维护） |
| `layer` | string | `builtin` | **第三方插件必须写 `third-party`**：`core` / `builtin` 只能随项目分发，导入时会被拒 |
| `category` | string | `""` | 管理界面的分组：`provider` / `tool` / `datasource` / `memory` / `media` … |
| `description` | string | `""` | 一句话说明（能力中心在详情里展示） |
| `homepage` | string | `""` | 来源地址（建议填：用户据此判断代码从哪来） |
| `loading_order` | int | `100` | 加载顺序，小者先。第三方插件建议 ≥500，排在全部内置能力之后 |
| `capabilities` | string[] | `[]` | 本插件贡献的能力面，见 §4 |
| `permissions` | object | 全空 | 权限声明，见 §6。**默认什么都不许** |
| `settings_schema` | object | `{}` | 配置的 JSON Schema，宿主据此渲染表单，见 §7 |
| `entry` | string \| null | `null` | 入口文件名（相对插件目录）。目录插件必须填，通常 `"plugin.py"` |
| `enabled` | bool | `true` | 默认是否启用（用户在界面上的启停会落盘并覆盖它） |

最小示例：

```json
{
  "id": "plugin-hello",
  "display_name": "示例插件",
  "version": "0.1.0",
  "layer": "third-party",
  "category": "tool",
  "description": "演示插件接口：注册一个工具，并读一条用户配置",
  "homepage": "https://github.com/your-name/hypra-plugin-hello",
  "loading_order": 500,
  "capabilities": ["tool", "settings"],
  "permissions": {
    "filesystem": { "read": [], "write": false },
    "network": { "hosts": [] }
  },
  "settings_schema": {
    "type": "object",
    "properties": {
      "greeting": { "type": "string", "title": "打招呼用的称呼", "default": "你好" }
    }
  },
  "entry": "plugin.py",
  "enabled": true
}
```

写完之后可以自检一遍（可选）：

```python
from app.plugins.sdk import load_manifest
load_manifest("D:/my-plugins/plugin-hello")   # 不合法会直接抛错
```

## 3. 入口契约：`build(ctx)`

宿主在**每次 setup** 时调用它（启用插件、保存配置、重扫目录都会重跑），把返回的 dict
按固定的桶收走。

```python
def build(ctx: PluginContext) -> dict:
    return {...}
```

**必须是幂等的**：按当前 `ctx.settings` 重新算一遍贡献即可。不要在这里做一次性副作用
（写文件、开线程、连服务）——那些属于 `startup`，`build` 只负责"声明我要贡献什么"。

返回值里宿主认识的键（其余键**静默忽略**，防插件写错别字导致启动失败）：

| 键 | 类型 | 作用 | 能力面 | 宿主消费状态 |
| --- | --- | --- | --- | --- |
| `tools` | `list[ToolSpec]` | 注册 function calling 工具，模型可自主调用 | `tool` | ✅ 已接通 |
| `datasources` | `list[DataSource]`（对象需有 `read() -> DataSourceSnapshot`） | 只读接入外部数据 | `datasource` | ✅ 已接通 |
| `startup` | `Callable[[], None]` | setup 之后调用（开线程 / 连服务放这里） | — | ✅ 已接通 |
| `shutdown` | `Callable[[], None]` | 关闭时调用（与 `startup` 配对释放资源） | — | ✅ 已接通 |
| `providers` | `dict[str, Callable]`，如 `{"llm": create_llm_provider}` | 按能力名提供实现工厂 | `provider` | ⚠️ 已登记为能力账本；业务侧目前仍直连工厂（`AGENTS.md §9.11`） |
| `prompt_fragments` | `list` | 往注入点插提示词片段 | `prompt` | ⚠️ 宿主已接收存储，**暂无消费方** |
| `hooks` | `dict[str, Callable]` | 订阅对话生命周期事件 | `hook` | ⚠️ 宿主已接收存储，**暂无消费方** |

"⚠️"两行不是摆设，是**当前的真实状态**：写进去不会报错、也不会生效。
如果你要的能力还没接通，先按 `datasource` / `tool` 这两条已通的路做。

`CONTRIBUTION_KEYS`（`app.plugins.sdk`）就是上表的键名元组，可在自检时对照。

## 4. 能力面（capability）

`manifest.capabilities` 描述"贡献什么"，与"必需性分层"是两个正交维度（`AGENTS.md §9.4`）。

| capability | 含义 | 产出类型 |
| --- | --- | --- |
| `tool` | 注册 function calling 工具 | `ToolSpec` |
| `datasource` | 读取外部数据 → **中性结构化对象** | `DataSourceSnapshot` |
| `provider` | 提供某类能力的实现（按接口注册） | 工厂函数 |
| `prompt` | 往注入点插提示词片段 | 见上表说明 |
| `hook` | 订阅对话生命周期事件 | 见上表说明 |
| `settings` | 声明式配置（JSON Schema） | `settings_schema` |

### 4.1 `datasource` 的分工是硬约定

**插件只负责"读 + 产出中性数据"，映射到宿主模型（世界书条目 / persona / 记忆）一律由宿主完成。**
这样多数据源共享同一套模型：插件不需要知道 `WorldBookEntry` 长什么样，宿主也不需要知道
任何外部文件格式。

中性契约在 `app.plugins.sdk`：

| 类型 | 用途 |
| --- | --- |
| `DataSourceSnapshot` | 一次读取的完整产出：`entries` / `characters` / `sessions` / `warnings` |
| `DataSourceEntry` | 一条可注入的设定条目（世界书 / lorebook）：`title` / `content` / `keys` / `constant` / `position` / `depth` / `role` / `order` / `probability` / `extra` |
| `DataSourceCharacter` | 一个外部角色卡 → 映射为 persona |
| `DataSourceSession` / `DataSourceMessage` | 一个外部会话 → 抽取为长期记忆 |

来源特有的字段一律放 `extra` **透传**（宿主不强解释、原样保留），避免把某一家的格式写死进契约。

参考实现：`backend/plugins/tavern-bridge/plugin.py`（真实酒馆数据的只读接入，含 PNG 角色卡解析）。

### 4.2 `tool`：给模型一个能调用的动作

```python
from pydantic import BaseModel
from app.plugins.sdk import ToolSpec


class NoArgs(BaseModel):
    """无参数。"""


def handler(args, context) -> dict:
    """args 是 NoArgs 实例，context 是 ToolContext（含 companion_id / session_id / extras）。"""
    return {"message": "工具执行完了"}


def build(ctx):
    return {
        "tools": [
            ToolSpec(
                name="hello_probe",          # 工具名（模型看到的就是它，建议带插件前缀避免撞名）
                description="什么时候该用这个工具——这句话直接决定模型会不会调用它",
                parameters=NoArgs.model_json_schema(),
                args_model=NoArgs,           # 宿主用它校验模型给的参数
                handler=handler,
                tags=["demo"],
            )
        ]
    }
```

要点：

- `description` 是给**模型**看的，写清"什么时候用"比写清"做什么"更重要；
- `handler` 抛异常不会中断对话：宿主把错误信息当作结果回传，模型可自然应对；
- 工具名冲突时后注册的会被前一个盖住——用 `mcp__<server>__<tool>` 这类带命名空间的名字最稳。

## 5. `PluginContext`：宿主交给你的门面

`build(ctx)` 的 `ctx` 是 `PluginContext`（`app/plugins/sdk.py`）。插件**不直接 import 业务模块**，
需要什么从 ctx 取——这样宿主换实现不影响插件。

| 成员 | 说明 |
| --- | --- |
| `ctx.settings` | `dict`：用户在界面上填的配置（按 `settings_schema` 落盘在 `data/plugins/<id>/settings.json`） |
| `ctx.logger` | 带插件名前缀的 `logging.Logger`（`plugin.<id>`），排查时按前缀过滤 |
| `ctx.manifest` | 本插件的 `PluginManifest` |
| `ctx.layer` | 本插件所属层 |
| `ctx.is_read_only` | 是否只读插件（`permissions.filesystem.write == false`） |
| `ctx.data_dir` | `Path`：宿主数据目录（如 `backend/data`） |
| `ctx.own_data_dir()` | `Path`：本插件自己的状态目录（`data/plugins/<id>/`），**无需额外声明权限** |
| `ctx.read_text(p)` / `ctx.read_bytes(p)` / `ctx.read_json(p)` | 受**读权限白名单**约束的读取 |
| `ctx.list_dir(p)` | 受读权限约束的列目录 |
| `ctx.write_text(p, text)` | 受**写权限**约束的写入（只读插件调用会抛 `PermissionError`） |
| `ctx.provider(capability, name="")` | 取宿主中某能力的实现（插件之间不直接依赖，只经此取用） |

**不要绕过 ctx 直接 `open()`**：那会跳过权限校验，让 manifest 里的声明变成一句空话。
权限是宿主强制的架构约束，绕过它等于自己拆掉用户的信任基础。

## 6. 权限声明（架构约束，不是口头承诺）

```json
"permissions": {
  "filesystem": {
    "read": ["${data_dir}/my-shared-assets", "${my_dir}"],
    "write": false,
    "write_paths": []
  },
  "network": { "hosts": [] }
}
```

| 字段 | 说明 |
| --- | --- |
| `filesystem.read` | 允许**读**的路径白名单。空 = 不读任何路径（`ctx.read_*` 一律拒绝） |
| `filesystem.write` | 是否允许写文件系统。默认 `false`（只读插件） |
| `filesystem.write_paths` | 允许**写**的路径白名单，仅当 `write: true` 时生效 |
| `network.hosts` | 允许访问的主机白名单。空 = 不访问网络 |

白名单项支持两类占位符：

- **插件自己的配置键**：`${my_dir}` → `ctx.settings["my_dir"]`。用户没配时该项连同占位符一起跳过（拿不到就不给读）；
- **宿主内置变量** `${data_dir}`：宿主数据目录。插件常需要读宿主放在 `data` 下的公共资源，
  而这个位置是宿主决定的，让 manifest 写死相对路径不合理。

三条硬规则：

1. **只读插件不得声明 `write_paths`**——声明自相矛盾会在注册阶段被拒（`Permission.check_consistent`），
   表现是插件被标记 FAILED 并常驻在能力中心里；
2. **未声明 = 不许**：权限缺省最严，别指望"没写就能用"；
3. **网络白名单目前没有强制闸门**：`ctx` 只对文件系统做了受控 API。
   声明 `hosts: []` 却真去发请求，等于把声明写成空话——请保持诚实：
   要么别发请求，要么在 `homepage` / README 里写清它要联网。

## 7. 配置：`settings_schema`（声明式 UI）

插件**不写前端代码**，只给一份 JSON Schema，宿主在能力中心的详情里渲染表单
（`AGENTS.md §9.5`：对标 Alife 的 `editorUI`，但避免了运行时加载前端代码）。

宿主**真正会渲染**的子集：`type`（string / number / integer / boolean / array / object）、
`title`、`description`、`enum`、`format`（`password` 用密码框）、`default`、
`items`、`minimum`、`maximum`、`required`。用了更复杂的组合（`oneOf` / `allOf` / `$ref`）
会降级成只读 JSON 预览——不会静默丢字段。

```json
"settings_schema": {
  "type": "object",
  "properties": {
    "data_dir": { "type": "string", "title": "数据目录", "description": "留空则不读取" },
    "api_key":  { "type": "string", "title": "API Key", "format": "password" }
  },
  "required": ["data_dir"]
}
```

三条约定：

1. **同名即覆盖宿主配置**：schema 键名与宿主 `Settings` 字段同名时，该值成为**运行时覆盖**
   （语义：界面设置 > `.env`）。能覆盖的字段由宿主白名单决定（`RUNTIME_OVERRIDABLE_FIELDS`），
   越界键忽略并告警；
2. **`format: "password"` 的字段永不回传明文**：GET 只回"有没有配"（`secrets_set`），
   PUT 留空 = 沿用、`null` = 清除；
3. 配置落在 `data/plugins/<id>/settings.json`——**与插件代码分离**：删掉 / 替换插件目录，
   用户填过的配置还在。

## 8. 怎么把插件接进程序

### 8.1 零拷贝（开发期推荐）

把插件目录的**父目录**填进 `.env`：

```ini
PLUGIN_EXTRA_DIRS=D:/my-plugins,../hypra-plugins
```

绝对路径原样使用；相对路径按数据目录（`data/`）解析。重启后端 → 能力中心「添加插件」→
「重新扫描」。宿主直接扫你的目录，**不碰你的源文件**，改完代码再点一次「重新扫描」即可。

### 8.2 导入（复制一份进程序）

适合"拿到一份插件包，想装进程序里"。在能力中心「添加插件」里填目录路径，或直接调接口：

```http
POST /plugins/import
{ "path": "D:/my-plugins/plugin-hello", "replace": false }
```

导入会做这些事（任何一条不过就**什么都不写**）：

- `manifest.json` 能解析、`id` 合法、`layer` 是 `third-party`；
- 权限声明自洽（只读却声明写路径 → 拒）；
- 入口文件存在且能 `ast.parse`（语法预检）；
- `id` 没被内置插件占用；目标目录不存在（除非 `replace: true`）。

写入用"暂存目录 → 整体替换"：写一半的中断不会留下半个插件目录；
`replace` 失败会把原来的那份放回去。

### 8.3 启用

导入 / 扫描只是"发现"，插件这时是**已禁用**状态。在能力中心里点启用才会真正 import 并执行它的代码。

## 9. 生命周期与失败隔离

```
discover（只读 manifest，不执行代码）
  → setup（构造 ctx，调用 build(ctx) 收贡献，调用 startup）
    → start
      → shutdown（逆序调用，释放资源）
```

- **发现阶段不执行任何插件代码**——只读 `manifest.json`，所以"插件列表"永远能显示出来；
- `build()` 会**重复调用**（启用 / 保存配置 / 重扫都会重跑），必须幂等；
- **任一插件失败都不阻断宿主启动**：失败的插件被标记 `FAILED`、错误原因记在状态里，
  在能力中心能看到具体原因，其余插件照常工作；
- **没有热重载**（`AGENTS.md §9.7`）：改了 `plugin.py` 的代码需要重启后端（开发期用
  `uvicorn --reload`）；"重新扫描"只重新读 manifest，**不会重跑已加载插件的代码**。
  但改了**配置**是即时生效的（宿主会重跑 setup）。

## 10. 调试

| 想看什么 | 怎么做 |
| --- | --- |
| 插件有没有被认出来 | 能力中心「添加插件 → 重新扫描」，看返回的"发现 N 个插件" |
| 为什么没生效 | 能力中心里看那一行的状态与错误原因（`FAILED` 会显示原因） |
| 插件自己的日志 | 日志前缀 `plugin.<id>`（`ctx.logger.info(...)`） |
| 权限被拒 | 异常里会写明"允许范围"，对照 `manifest.permissions` 补声明 |
| 配置有没有读对 | 能力中心详情里的表单（密钥字段只显示"已配置"） |

## 11. 限制与红线

| 项 | 说明 |
| --- | --- |
| **没有沙箱** | 插件代码在宿主进程内执行。装一个插件 = 信任它的作者，与手动 `pip install` 一个包是同一性质的风险。界面不会（也不该）暗示有隔离 |
| **静态检查只到语法** | 宿主只做 `ast.parse` 与 manifest 校验，**不做**语义安全判断。"看起来检查过了"不等于安全 |
| **不做热重载** | 见 §9。开发期用 `uvicorn --reload` |
| **不自动装依赖** | 插件需要的第三方包请让用户自己装，并在 README 里写出 `pip install ...`；缺依赖时 `setup()` 失败，能力中心会显示原因 |
| **不做签名 / 信任根** | 本地可信边界下没有实际收益（`AGENTS.md §9.7`） |
| **插件不自带前端 UI** | 用 §7 的 JSON Schema 表达配置；`ui_slots` 在 v1 明确不做 |
| **许可** | 进程内耦合的插件视为衍生作品，须以 **AGPL 兼容协议**授权（`AGENTS.md §6`） |
| **只写用户目录** | 插件与它的配置都落在 `data/`（已 gitignore）；`backend/plugins/` 是随项目分发的第一方内容，运行时永不改写 |

## 12. 完整示例

[`docs/examples/plugin-hello/`](examples/plugin-hello/) 是一个可以直接跑的最小插件：

```
plugin-hello/
  manifest.json     # 声明 tool + settings 两个能力面，只读、不联网
  plugin.py         # build(ctx) 注册一个打招呼工具，读一条用户配置
  README.md         # 怎么装、怎么验证
```

把它复制到 `data/plugins/` 下（或填进 `PLUGIN_EXTRA_DIRS`），重新扫描 → 启用 →
在对话里让模型调用它，即可验证整条链路。

更完整的参考实现（真实数据、权限边界、`datasource` 契约）：

| 插件 | 看什么 |
| --- | --- |
| `backend/plugins/tavern-bridge/plugin.py` | `datasource` + `tool` + 权限白名单的实际用法 |
| `backend/plugins/live2d-model-source/plugin.py` | 最简形态：只贡献一个 datasource，只读 |
| `backend/app/plugins/builtin.py` | 宿主自己怎么用代码内注册的方式提供 provider 族 |
