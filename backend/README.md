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
