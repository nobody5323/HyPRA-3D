# HyPRA 后端

分层提示词 + 混合记忆（热/温/冷）+ 情绪识别 + 数字人驱动的 FastAPI 服务。

## 目录

    app/
      api/           # FastAPI 路由（chat 已落地，memory/media 按需新增）
      session/       # 会话：上下文 + 内存存储（热层滚动窗口载体）
      prompts/       # 分层 System Prompt：人设预设 / 状态变量 / 世界书注入编排 / 渲染管道
      worldbook/     # 世界书：条目模型 / YAML 加载 / 关键词+正则触发匹配
      memory/
        cold/        # 冷层：SQLite 结构化事实表 + 滚动增量摘要
        warm/        # 温层：WarmMemoryStore 接口 + 时间衰减 + 确定性 embedding + 内存实现
      llm/           # LLM 抽象：接口 / mock / 工厂 / 模型预设档（profiles.yaml）
      tools/         # function calling 工具（内置：情绪日记/趋势/呼吸/记忆检索）
      mcp/           # MCP Client：把外部 MCP server 的工具接入 Agent 行动层
      config.py      # 配置（backend/.env 读取，双模式：本地 Docker 优先，云可切）
      main.py        # 应用工厂 + /health + 路由注册
    tests/           # pytest（91 项，全离线无外部依赖）
    data/            # 本地 SQLite 数据库（gitignore，不入库）

> 开发/评审双模式与一键部署见根目录 [docs/deployment.md](../docs/deployment.md)。

## 模型预设（per-model）

不同模型的采样容忍度与推理链默认行为不同，配置集中在 `app/llm/profiles.yaml`：

- **选择优先级**：接口 `preset_id` 显式指定 > 按模型名 `match` 匹配（最长子串） > `default` 通配
- **每档包含**：采样参数（temperature / top_p / frequency_penalty / presence_penalty / max_tokens）、
  推理开关（`enable_thinking`，关闭后推理模型实测提速约 6 倍）、技术性输出约束（`style_hint`）
- **合并规则**：文风预设的 sampling **覆盖**模型档（文风更贴近内容意图），模型档独有键保留
- **接口**：`GET /chat/presets` 返回档位清单 + 按当前 `LLM_MODEL` 自动匹配到的 `auto_preset_id`；
  `POST /chat` 可传 `preset_id` 手动指定

### 导入 SillyTavern 预设（合规方式）

```bash
python scripts/import_st_preset.py <preset.json> --preset-id deepseek --model-match deepseek
```

该脚本**只提取采样参数与槽位骨架（字段名/角色/注入位置/启用状态）**，
**丢弃全部提示词正文**并打印丢弃统计 —— 这是 `AGENTS.md §6`「借鉴社区插件只取
机制思想，不复制代码与提示词原文」的落地方式。产出的草稿需人工复核后再并入 `profiles.yaml`。

## MCP 接入（外部能力进入 Agent 行动层）

HyPRA 作为 **MCP Client** 连接外部 MCP 服务器，把它们的工具桥接进 `ToolRegistry`，
模型即可像调用内置工具一样调用外部能力。

```
app/mcp/
  config.py   服务器清单（兼容 Claude Desktop 的 mcpServers 写法；支持 ${PYTHON}/${BACKEND_DIR} 占位符）
  session.py  常驻会话：后台事件循环线程 + run_coroutine_threadsafe 同步门面
  bridge.py   MCP 工具 → ToolSpec（命名 mcp__<服务器>__<工具>）
  manager.py  生命周期单例：启动连接 / 关闭断开 / 注册工具 / 状态快照
```

**配置**：`backend/mcp_servers.json`（不存在 = 未配置，等同不启用）

```json
{
  "mcpServers": {
    "wellness": {
      "command": "${PYTHON}",
      "args": ["${BACKEND_DIR}/mcp_servers/wellness_server.py"]
    }
  }
}
```

`.env` 可调：`MCP_ENABLED` / `MCP_SERVERS_FILE` / `MCP_CONNECT_TIMEOUT` / `MCP_CALL_TIMEOUT`。

**为什么需要 `session.py` 这层桥接**：MCP 官方 SDK 是 asyncio 的（且内部用 anyio
task group，必须在同一存活任务内使用），而 HyPRA 的编排链是同步的
（LangGraph 节点 → `ToolRegistry.execute` → handler）。这里用一个**常驻后台事件循环线程**
承载会话，同步侧 `run_coroutine_threadsafe(...).result(timeout)` 调用，
好处是无论调用多少次工具都复用同一条连接（stdio 子进程只启动一次）。

**自带演示服务器**：`mcp_servers/wellness_server.py`（100% 原创）提供
「情绪 → 环境音推荐」「压力 → 呼吸节奏建议」，用于演示「数字人通过 MCP 调用外部能力办事」。

**观测**：`GET /health` 返回 MCP 连接状态与工具清单。

**容错**：任一服务器连接失败只告警不阻断启动；工具调用异常由 `ToolRegistry` 隔离，
对话不中断。

## 本地开发（零云端 key 可跑通链路）

```bash
# 在项目根目录创建虚拟环境并安装（在 backend/ 内执行）
cd backend
python -m venv ../.venv
../.venv/Scripts/pip install -e ".[dev]"
cp .env.example .env    # 默认 mock LLM + 本地 embedding，无需任何 key
```

运行：

```bash
../.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

验证：

```bash
curl http://localhost:8000/health
```

测试：

```bash
../.venv/Scripts/python -m pytest
```
