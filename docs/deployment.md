# HyPRA 部署说明（评审交付 · docker compose 一键部署）

> 目标：评委拿到代码后，**一条命令拉起后端 + 本地向量库**，只需自填
> embedding / LLM 的云端 key 即可完整对话。

## 1. 双模式总览

| 模式 | 场景 | 向量库 | embedding / LLM |
|---|---|---|---|
| 开发模式 | 本地日常开发 | 云 Qdrant 或 `WARM_BACKEND=memory` | 开发者自备 key |
| **评审模式** | 评委验收 | **docker compose 本地 Qdrant** | 评委在 `.env` 自填 |

两种模式共用同一份代码，全部差异收敛在 `backend/.env` 配置（provider/key/model/URL）。

> **本地日常开发不用手敲命令**：根目录 `start.bat`（默认后端 + 桌面程序控制台）
> 会做环境自检、起本机 Qdrant（若 `.env` 用 `WARM_BACKEND=qdrant` 且指向本机）、
> 等后端就绪；`start.bat web` 则是后端 + Web 前端。参数见 [README](../README.md) 的「一键启动」。

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
| `qdrant` | `qdrant/qdrant:v1.19.1` | 6333 | 本地向量库（无 key，评审零配置） |
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
不填也能演示：自动降级为**本地渲染器（Live2D / 立绘）+ 静默**，对话/字幕/情绪不受影响。

## 6. 零依赖兜底（无 key 也能演示）

若评审环境不便联网：
```env
WARM_BACKEND=memory            # 内存假向量库
LLM_PROVIDER=mock              # 占位回复（无 key 可跑通链路）
EMBEDDING_PROVIDER=deterministic
```
此时链路完整可用（会话 / 世界书 / 渲染照常），前端只是**不播报语音**，
仅模型回复为占位文本。

## 6.5 可选：自部署 GPT-SoVITS（给 Live2D / 静态立绘配真声音）

**不部署也完全可用**：未配置时驱动自动降级（`has_audio=false`），
前端逐句静音（对话与字幕不中断）。本节只给想开这能力的人看。

```bash
# ① 拉官方仓库并起服务（需要 NVIDIA GPU；默认端口 9880）
git clone https://github.com/RVC-Boss/GPT-SoVITS && cd GPT-SoVITS
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

# ② 准备一段参考音频（5 秒左右即可零样本克隆），例如放到 GPT-SoVITS/refs/xiaolin.wav
```

```env
# ③ backend/.env：只改这两处
DIGITAL_HUMAN_PROVIDER=gpt_sovits
GPT_SOVITS_BASE_URL=http://127.0.0.1:9880
# 注意：这是**跑 GPT-SoVITS 那台机器**上可见的路径，不是本机相对路径
GPT_SOVITS_REF_AUDIO=refs/xiaolin.wav
GPT_SOVITS_PROMPT_TEXT=参考音频里说的那一句话
```

**多音色**：把音色写在 `backend/data/tts_voices.json`（属本地数据，已被 gitignore 覆盖）：
```json
{
  "_emotion_map": { "sad": "gentle", "tired": "gentle", "happy": "lively" },
  "gentle": { "ref_audio_path": "refs/gentle.wav", "prompt_text": "今天也辛苦了。", "label": "温柔" },
  "lively": { "ref_audio_path": "refs/lively.wav", "prompt_text": "我们出发吧！", "label": "活泼" }
}
```
改完文件需**重启后端**（前端「数字人设置」里会提示这一点）；前端可在
「数字人设置 → 语音引擎 / 音色」里选择，并能点「试听」。

**参考音频的硬要求（实测确认）**：
| 项 | 要求 |
|---|---|
| 时长 | **必须 3~10 秒**（服务端会拦：`参考音频需在3~10秒范围外，请更换！`） |
| `ref_audio_path` | **必填**：不传直接 `400 ref_audio_path is required`（`ref_free` 在这个版本无效） |
| `prompt_text` | 与音频内容**逐字一致**时相似度最佳；**留空也能用**（实测可正常合成，速度更快、相似度略降） |
| 路径语义 | 服务端可见的路径（跑 GPT-SoVITS 那台机器上的路径）；支持正斜杠 `D:/.../x.wav` |

**情绪 → 音色（声随情变）**：音色表里的保留键 `_emotion_map`（`_` 开头的键不会被当作音色）
把后端 8 类情绪标签映射到音色 id，于是「不指定 voice 时按本轮情绪选声音」：
```
优先级：显式 voice  >  情绪映射  >  GPT_SOVITS_DEFAULT_VOICE  >  默认参考音频
```
指向不存在音色的映射会在加载时被丢弃并记 warning；响应里的
`meta.voice_source`（`request` / `emotion` / `default` / `config`）能一眼看出这次声音是谁选的。

**性能相关配置（都有实测依据）**：

| 配置 | 作用 | 实测 |
|---|---|---|
| `GPT_SOVITS_WARMUP=true`（默认） | 启动时在后台线程预热一次 | 预热耗时 1.5s；避免服务刚起来时首个请求偏慢 |
| `GPT_SOVITS_EXTRA_PARAMS` | 透传 api_v2 调优参数（核心字段不可覆盖） | `{"parallel_infer": false}` → 2.74s vs 默认 2.99s |
| ⚠️ 别再试 `{"sample_steps": 8}` | — | **实测无效**：8/16/32 分别 3.13 / 2.80 / 3.18s |
| `GPT_SOVITS_MEDIA_TYPE=wav` | 能解析出时长 → 口型按真实音频对齐 | 非 wav 时 `meta.duration_source=estimated`（口型退化为估算） |

> 合成耗时随文本长度**近似线性**：15 字 ≈ 3.0s、46 字 ≈ 4.8~8.8s、96 字 ≈ 8.6s。
> 因此前端默认开启分句流水线（首句 2~3s 出声），细节见
> [frontend-avatar-integration.md](frontend-avatar-integration.md) 第八章。

**排查**：
| 现象 | 原因 |
|---|---|
| 完全没有声音 | 后端降级了：看 `/media/tts/voices` 的 `note`（多为未配参考音频 / 服务没启动）。浏览器语音已移除，不会再有「系统音色顶上」 |
| `GET /media/tts/voices` 返回 `configured: false` | `GPT_SOVITS_REF_AUDIO` 与音色表都没配 |
| 合成报 `参考音频需在3~10秒` | 参考音频太长/太短（实测 70 秒的示例会被拒） |
| 音频能合成但取不到 | `GPT_SOVITS_MEDIA_TYPE` 别选 `raw`（裸 PCM，浏览器放不了） |

> ⚠️ **合规**：音色克隆只使用自己录制或已获明确授权的声音，不要克隆他人声音。

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
| `DIGITAL_HUMAN_PROVIDER` | `local` | `xmov`（魔珐渲染+自带 TTS）/ `gpt_sovits`（仅出声音） |
| `GPT_SOVITS_BASE_URL` | `http://127.0.0.1:9880` | 自部署 GPT-SoVITS 地址 |
| `GPT_SOVITS_REF_AUDIO` | 空 | 参考音频路径（决定音色；不填=不出服务端音频） |
| `GPT_SOVITS_VOICES_FILE` | `data/tts_voices.json` | 多音色表（可选） |
