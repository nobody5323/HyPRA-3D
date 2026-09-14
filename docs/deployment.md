# HyPRA 部署说明（评审交付 · docker compose 一键部署）

> 目标：评委拿到代码后，**一条命令拉起后端 + 本地向量库**，只需自填
> embedding / LLM 的云端 key 即可完整对话。

## 1. 双模式总览

| 模式 | 场景 | 向量库 | embedding / LLM |
|---|---|---|---|
| 开发模式 | 本地日常开发 | 云 Qdrant 或 `WARM_BACKEND=memory` | 开发者自备 key |
| **评审模式** | 评委验收 | **docker compose 本地 Qdrant** | 评委在 `.env` 自填 |

两种模式共用同一份代码，全部差异收敛在 `backend/.env` 配置（provider/key/model/URL）。

## 2. 前置条件（评审机）

- 已安装 **Docker**（含 docker compose 插件）
- 网络可达国内模型托管 API（百炼 / 硅基流动 等，用于填 key）

## 3. 一键部署步骤

```bash
# ① 克隆/解压项目到本地
git clone <repo-url> hypra && cd hypra

# ② 配置（评审只需填自己的 key，其余默认零依赖）
cd backend
cp .env.example .env
#   编辑 .env：
#     WARM_BACKEND=qdrant            ← 使用 compose 内置本地 Qdrant
#     QDRANT_URL=http://qdrant:6333  ← 容器内互联地址（无需 key）
#     LLM_PROVIDER=dashscope         ← 自选：dashscope | siliconflow | openai-compatible
#     LLM_API_KEY=sk-xxx             ← 自填
#     EMBEDDING_PROVIDER=dashscope   ← 与 LLM 同源亦可
#     EMBEDDING_API_KEY=sk-xxx       ← 自填
cd ..

# ③ 一键启动（构建后端 + 前端镜像，并拉起本地 Qdrant）
docker compose up -d --build

# ④ 验证
curl http://localhost:8000/health            # 后端健康检查 → {"status":"ok",...}
docker compose ps                            # 三个服务：qdrant / backend / frontend
# 打开 http://localhost:3000                ← 前端对话页（数字人舞台 + 对话面板）
# 打开 http://localhost:8000/docs           ← 后端接口调试（POST /chat）
```

## 4. 服务编排（docker-compose.yml）

| 服务 | 镜像/来源 | 端口 | 说明 |
|---|---|---|---|
| `qdrant` | `qdrant/qdrant:v1.12.4` | 6333 | 本地向量库（无 key，评审零配置） |
| `backend` | 本地 Dockerfile 构建 | 8000 | FastAPI 应用（QDRANT_URL 指向 qdrant 服务） |
| `frontend` | 本地 Dockerfile 构建 | 3000 | Next.js 对话 UI + 魔珐数字人舞台 |

启动顺序由健康检查保证：`qdrant`（healthy）→ `backend`（healthy）→ `frontend`，
即前端启动时后端已就绪，首屏不会显示「后端未连接」。

> `frontend` 的 `NEXT_PUBLIC_API_BASE` 通过 `build.args` 传入（构建期内联）：
> 请求由**宿主机浏览器**发起，必须填宿主机可达地址 `http://localhost:8000`，
> 不能填容器名 `http://backend:8000`（浏览器无法解析容器名）。

## 5. 前端密钥与 CORS（评审常见坑）

三处易踩的坑，均已处理，评审无需额外操作：

| 坑 | 说明与处理 |
|---|---|
| 浏览器访问后端 | 前端请求在浏览器发起，API 地址必须是宿主机可达的 `http://localhost:8000`（compose 已固定） |
| 变量是**构建期内联** | `NEXT_PUBLIC_*` 在 `docker build` 时写入前端产物；改动后需 `docker compose build frontend` 重建（改 `.env` 重启无效） |
| CORS | 浏览器从 `:3000` 访问 `:8000` 属跨域，后端默认已放行 `http://localhost:3000`；若自定义了 `CORS_ORIGINS`，需重启 backend 容器 |

**魔珐星云数字人密钥无需在 compose 里配置**：打开页面右上角「数字人设置」
填入 App ID / Secret 即可（存浏览器 localStorage，即时生效、不落盘、不入镜像）。
不填也能演示：自动降级为**浏览器原生 TTS + 占位形象**，对话/字幕/情绪不受影响。

## 6. 零依赖兜底（无 key 也能演示）

若评审环境不便联网：
```env
WARM_BACKEND=memory            # 内存假向量库
LLM_PROVIDER=mock              # 占位回复（无 key 可跑通链路）
EMBEDDING_PROVIDER=deterministic
```
此时链路完整可用（会话 / 世界书 / 渲染照常），前端也会自动降级为浏览器 TTS，
仅模型回复为占位文本。

## 7. 后端配置键位速查

| 键 | 默认 | 评审模式建议 |
|---|---|---|
| `WARM_BACKEND` | `memory` | `qdrant` |
| `QDRANT_URL` | `http://localhost:6333` | `http://qdrant:6333` |
| `QDRANT_API_KEY` | 空 | 空（本地无鉴权） |
| `LLM_PROVIDER` | `mock` | `dashscope` / `siliconflow` / `openai-compatible` |
| `LLM_API_KEY` / `LLM_MODEL` | 空 / qwen2.5-7b | 自填 |
| `EMBEDDING_PROVIDER` | `deterministic` | 与 LLM 同源提供商 |
| `EMBEDDING_API_KEY` | 空 | 自填 |
