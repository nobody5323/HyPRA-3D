# 前端接入说明：魔珐星云数字人（M5）

> 面向前端（Next.js）开发者：如何把后端产出的**播报指令**交给魔珐 SDK，
> 让数字人「说人话、带情绪、有动作」。

## 一、整体架构

```
前端发一条用户消息
      ↓  POST /chat
后端（HyPRA）
  ├─ 对话链路：人设 + 世界书 + 记忆 + 文风 → LLM 生成回复
  ├─ 情绪链路：function calling → 情绪标签（label/intensity/facial_expression）
  └─ 播报指令：回复 + 情绪 → SSML（含 KA 动作）
      ↓  响应：{ reply, emotion, speak: { ssml, display_text, ka_action, tone, voice } }
前端
  └─ avatar.speak(speak.ssml, true, true)   ← SDK 内部完成 TTS + 口型 + 表情 + 动作 + 渲染
```

**职责边界**：后端只产出**文本与指令**，渲染与语音由魔珐 SDK 在前端完成。

---

## 二、准备工作：获取 appId / appSecret

1. 登录 [魔珐星云](https://xingyun3d.com/) 控制台
2. 进入**应用中心** → 创建**驱动应用**（选择角色、音色、表演风格）
3. 应用创建后 → **查看密钥** → 复制 **App ID** 与 **App Secret**

> ⚠️ 注意：`/media/speak` 等后端接口使用**同一对密钥**，但走的是服务端签名鉴权
> （X-APP-ID + X-TOKEN），密钥只放在 `backend/.env`，**不要写进前端代码**。

---

## 三、SDK 引入与初始化

```html
<!-- 1. 引入（注意关注版本，以获取最新特性） -->
<script src="https://media.xingyun3d.com/xingyun3d/general/litesdk/xmovAvatar@latest.js"></script>
```

```js
// 2. 创建实例
const avatar = new XmovAvatar({
  containerId: '#avatar-container',
  appId: '<由后端下发或构建时注入>',        // 或用后端签发的临时凭证
  appSecret: '<同上>',
  gatewayServer: 'https://nebula-agent.xingyun3d.com/user/v1/ttsa/session',
  hardwareAcceleration: 'prefer-hardware',
  onWidgetEvent(data) { console.log('Widget 事件:', data) },
});

// 3. 建立连接（进入待机）
await avatar.init();
```

> **安全提示**：`appSecret` 出现在前端属于官方 SDK 的设计；若部署到公网，
> 建议由后端签发**临时凭证/代理**，或限制域名来源。

---

## 四、完整对话流程（推荐写法）

```js
async function sendMessage(userText, sessionId) {
  // ① 调后端：拿到回复 + 情绪 + 播报指令
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: userText, session_id: sessionId, user_name: '小林' }),
  });
  const data = await res.json();

  // ② 渲染字幕 / 更新 UI
  renderSubtitle(data.speak.display_text);

  // ③ 驱动数字人说话（SDK 内部完成 TTS + 口型 + 表情 + KA 动作）
  avatar.speak(data.speak.ssml, /* is_start */ true, /* is_end */ true);

  // ④ 可选：用情绪驱动页面视觉（背景色、光效等）
  applyMoodTheme(data.emotion);   // { label, label_zh, intensity, facial_expression }

  return data;   // { reply, emotion, speak, memory_counts, style, ... }
}
```

### 播报指令字段说明（`/chat` 响应的 `speak` 字段）

| 字段 | 说明 |
|---|---|
| `ssml` | **SSML 播报文本**（含 KA 动作指令），直接传给 `avatar.speak()` |
| `display_text` | 字幕纯文本（已剥离 SSML 标签） |
| `ka_action` | 本次写入的动作标识（如 `comfort`；强度不足时为空） |
| `tone` | 语气描述（如「柔声、放缓」），用于 UI 提示，**不在 SSML 内** |
| `voice` | 音色 ID（`tts_vcn`） |

也可以单独调用：`POST /media/speak`（只生成播报指令，不跑对话链路）。

---

## 五、关键注意事项（官方文档要点）

| # | 注意点 | 说明 |
|---|---|---|
| 1 | **`speak` 不能连续调用** | 指**两次独立播报之间**：上一次 `is_end=true` 之后，需先用 `interactive_idle` 切换状态再播下一句。**同一次播报的多个流式片段之间不受此限**（见第 7 条） |
| 2 | **流式播报** | 对接大模型流式输出时：首句 `is_start=true`，末句 `is_end=true`，中间都为 `false`；建议首句积攒一小段内容再发 |
| 3 | **讲话状态（坑）** | 回调签名为 `onVoiceStateChange(state, duration, client_speak_id)`，但 **`state` 的实际取值是 `"start"` / `"end"`**——官方文档用事件名 `voice_start` / `voice_end` 描述，回调里并不是这两个字符串（源码 `EventDispatcher.dispatch` 的 `voice_state_change` 分支可证）。**只认 `"voice_end"` 会让事件永远匹配不上**，每段播报都只能靠计时兜底收尾，表现为**最后一句被切断、说不完**。建议两种写法都接受 |
| 4 | **资源释放** | 页面卸载前调用 `avatar.destroy()` |
| 5 | **状态** | SDK 公开方法为**小写无下划线**：`idle()` / `listen()` / `interactiveidle()` / `interrupt()`（早期文档示例写的 `setState('interactive_idle')` 与实现不符，已纠正） |
| 6 | **等待超时（坑）** | 若用计时兜底等播报结束，**别按 250ms/字估算**：中文 TTS 稍慢就被误判超时。本项目用 **400ms/字、最小 12s、上限 90s**，并在 `voice_end` 之后留 **600ms 尾音缓冲**再切回待机——`idle()` 内部会 `interrupt` 渲染调度，切太早会削掉尾音 |
| 7 | **流式 vs 多次独立播报** | `speak(ssml, is_start, is_end)` 中 **`is_start=true` 会先 `renderScheduler.interrupt()`**（源码可证）。「多次独立播报」必须靠 `interactive_idle` 过渡。⚠️ **本项目实测结论（别重蹈）**：曾依据源码里 `multi_turn_conversation_id` 只在 `is_end=true` 时自增，推断「同一话轮的片段会被服务端拼起来解析」，于是把分段 SSML 做成「**同一个 `<speak>` 文档的几片**」（首段带开标签、末段带闭标签、中间裸文本）并配 `(true,false)/(false,false)/(false,true)`。结果 **数字人完全不发声** —— 服务端是**逐条解析**每条 `send_text` 的 `ssml` 的，缺 `<speak>` 包裹的片段被判为无效 SSML。**已回退**：每段各自独立、完整的 `<speak>` 文档 + 每段 `(true,true)` + 段间 `interactive_idle`。由此的连带结论：**魔珐路径下分段播报做不到段间无缝**（每段都是独立话轮、服务端逐段从零合成，段间必有合成空档），所以前端对魔珐路径**不做分段**，整段一次播报（见 `useChatSession` 的 `fetchSpeechChunks` 调用条件） |
| 8 | **`onMessage` 是诊断通道，不是可用性判据** | SDK 把三类东西都从 `onMessage` 报出来：① 自恢复告警——`10002 CONNECT_SOCKET_ERROR`（"socket长时间未下发数据"，SDK 紧接着自己 `reStartSDK()`）、`10007 SOCKET_DISCONNECT`（字面 `client Warning: socket disconnect`，由前者的自恢复动作触发，socket.io 配了 `reconnectionAttempts: Infinity`）；② 可选能力不支持——`10005 INIT_ERROR` 的 5 个触发点里有 3 个是音频捕获 / Opus 编码（只影响 ASR 回声消除）；③ 服务端 `error_message` 原样透传的自定义错误码（`40006 TTSA_ERROR`）。**这些都不代表数字人渲染不可用**。⚠️ 本项目的「降级」会 `destroy()` SDK 且**不可逆**（用户须手动改密钥/换渲染方式才能回到 3D），因此**不**用 `onMessage` 触发降级，只记日志并写进 `detail`；「连不上」只认**启动路径**：SDK 脚本加载失败 / `init()` reject / 初始化超时（90s），经 `scheduleReconnect` 重试 3 次后才降级。另注：当前 SDK **不会**调用 `avatar.onError`（源码 24 处 `onError` 全是 WebGL / socket.io / mp4box 内部处理），该项目里那条回调目前是兜底 |

### 连续多轮对话的正确写法

```js
async function speakSafely(ssml) {
  // 若正在播报，先打断并回到交互待机，避免 speak 连续调用
  avatar.interrupt?.();
  avatar.interactiveidle?.();       // 官方公开方法（不是 setState('interactive_idle')）
  // 状态切换经 WebSocket 下发，需留出时间（实测 400ms 左右）；
  // 否则紧接着的 speak 会被 SDK 静默丢弃
  await new Promise((resolve) => setTimeout(resolve, 400));
  avatar.speak(ssml, true, true);
  await waitVoiceEnd(avatar);       // 监听 onVoiceStateChange → voice_end
}

// 分段播报（赛题表达层的「流式分段」）——**同一次播报的多个片段**，段间无缝。
//
// ⚠️ 关键：`is_start` / `is_end` 是**流式片段**的标志，不是「一段话」的标志。
// 依据（源码 `sendText`）：`multi_turn_conversation_id` 只在 `is_end === true`
// 时才 `updateUniqueSpeakId()` 自增，即同一话轮的片段会被服务端**拼起来**
// 当成一次播报解析。所以「每段都传 (true,true)」= 每段都是一个独立话轮
// → 服务端逐段从零合成 → 段与段之间出现合成空档（用户听到的「中间卡壳」）；
// 且 `is_start=true` 还会触发 `renderScheduler.interrupt()`，每段都打断一次。
//
// 正确写法：首段 (true,false) → 中间 (false,false) → 末段 (false,true)，
// 全部片段共享同一个 multi_turn_conversation_id，服务端连续合成，段间无缝；
// voice_end 也只在整段结束时下发一次（因此**只等一次**）。
// 片段之间**不需要** interactive_idle —— 那是「两次独立播报之间」才需要的过渡。
//
// 片段由后端 /media/speak（streaming=true）的 ssml_chunks 给出；它们是
// **同一个 `<speak>` 文档**的若干部分（见 backend/app/digital_human/ssml.py
// 的 build_streaming_ssml_chunks），前端不自拼标签。
async function speakChunks(ssmlChunks) {
  const last = ssmlChunks.length - 1;
  const pending = waitVoiceEnd(avatar);   // 先登记，避免 voice_end 早于登记到达
  for (let i = 0; i <= last; i += 1) {
    avatar.speak(ssmlChunks[i], i === 0, i === last);
    if (i < last) {
      // 片段之间的发送节奏（150ms/字）：字幕跟着声音走，且服务端不会「等文本」
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  await pending;
}
```

> 真实实现见 `frontend/hooks/avatar/useAvatar.ts` 的 `speakOnce` / `speak` / `speakChunks`
> （含 voice_end 丢失时的超时兜底，避免 await 永久挂起）。

### 断线重连

`onError` 不应直接永久降级：网络抖动或服务重启后应能自恢复。
本项目按**指数退避**重建 SDK（1s → 2s → 4s，最多 3 次），
期间界面显示 `reconnecting` 状态；重连用尽则降级为**静默**（保留失败原因，语音不再兜底）。

---

## 六、后端接口速查

| 接口 | 用途 | 说明 |
|---|---|---|
| `POST /chat` | 完整对话 | 返回 `reply` / `emotion` / `speak` / `memory_counts` / `knowledge_hits` / `memory_scheduled` / `worldbook_hits` / `style` / `preset` / `tools_used` |
| `GET /chat/presets` | 模型适配档清单 | 界面「模型预设」选择器数据源 |
| `GET /chat/personas` | 人设（陪伴对象）清单 | 人设 id 即记忆命名空间 `companion_id` |
| `GET /chat/styles` | 文风清单 | 界面「文风」选择器数据源 |
| `POST /media/speak` | 仅生成播报指令 | 参数：`text` / `emotion` / `intensity` / `voice` / `streaming` / `max_chars`；`streaming=true` 时额外返回 `chunks`（纯文本分段）与 `ssml_chunks`（逐段 SSML） |
| `POST /media/avatar` | **扩展路径**：口型/表情/动作**时间轴** + 可选音频 | 供自研渲染使用（见下节） |
| `GET /media/tts/voices` | 当前语音引擎与可选音色 | 前端据此决定用服务端音频还是**静默**（`server_tts` / `configured` / `voices` / `note`） |
| `GET /media/audio/{file}` | 音频文件 | 仅扩展路径产生音频时有值 |
| `POST /knowledge/upload` | 上传个人记忆 | multipart：`file` 或 `text`；query `companion_id`；form `title` / `force`；判重命中返回 409 + 判重详情 |
| `GET /knowledge/list` | 个人记忆文档列表 | query `companion_id` |
| `DELETE /knowledge/{doc_id}` | 删除文档及其分块 | query `companion_id` |
| `GET /health` | 健康检查 + 行动层状态 | 含 `mcp` 各服务器连接状态与工具清单 |

---

## 七、扩展路径：可接入任意 3D / 2D 模型

除魔珐 SDK 外，后端还提供**驱动时间轴**（`POST /media/avatar`），
让项目可以接入自研或其它渲染器（Three.js / Live2D / Unity WebGL 等）：

```json
{
  "duration_ms": 3200,
  "visemes": [{ "start_ms": 0, "end_ms": 180, "viseme": "A", "char": "我" }, ...],
  "face":    [{ "start_ms": 0, "end_ms": 300, "expression": "frowning_worry", "intensity": 0.8 }, ...],
  "body":    [{ "start_ms": 0, "end_ms": 1200, "gesture": "lean_in", "intensity": 0.8 }, ...]
}
```

- `visemes`：口型时间轴（10 种口型；若能拿到 TTS 字级时间戳则为**精确对齐**）
- `face`：表情时间轴（来自 M4 情绪链路的 `facial_expression` + `intensity`）
- `body`：动作时间轴（幅度随情绪强度变化）

这套接口是**渲染无关**的，因此同一份后端数据既可驱动魔珐 SDK，也可驱动任意模型。

当后端接了服务端 TTS 时，响应里还会多出音频：

```json
{
  "provider": "gpt_sovits",
  "has_audio": true,
  "audio_url": "/media/audio/tts_ab12cd34ef56.wav",
  "audio_format": "wav",
  "duration_ms": 3200,
  "visemes": [ ... ],
  "meta": { "voice": "gentle", "align": "estimated-scaled", "duration_source": "audio" }
}
```

- `audio_url` 是**相对路径**（后端不知道自己对外暴露的域名/端口）：
  前端必须用 `resolveMediaUrl()` 补全，否则请求会打到前端 3000 而 404；
- `meta.align` 是「口型精度自述」：`estimated-scaled` = 按文本估算再缩放到真实音频时长
  （GPT-SoVITS 不返回字级时间戳，做不到逐字对齐）；
- 列 `meta.duration_source` 为 `estimated` 时说明没拿到音频时长（非 WAV），口型仍是估算值。

---

## 八、语音从哪来（三条路径，可手动切换）

声音与渲染是**解耦**的：同一个形象可以换声音，换声音不影响对话 / 记忆 / 情绪链路。

| 路径 | 适用形象 | 声音来源 | 口型精度 |
|---|---|---|---|
| 魔珐 SDK 自带 TTS | 魔珐 3D 形象 | 魔珐控制台配置的音色（`XMOV_VOICE`） | 字级时间戳，逐字对齐 |
| 服务端 TTS（GPT-SoVITS） | Live2D / 静态立绘 | 自部署 GPT-SoVITS + 音色表 | 整段对齐（估算轨道按音频时长缩放） |
| 静默（`none`） | 任意本地渲染 | **无声音** | —（未接入 TTS 时不播报，只显示文字与字幕） |

**为什么魔珐路径不能配外部 TTS**：SDK 的 `avatar.speak(ssml)` 只接受文本（SSML），
不接受外部音频；即「魔珐形象 + GPT-SoVITS 声音」当前不可行，
要么用魔珐音色，要么用自研渲染器（`POST /media/avatar` 的音频 + 时间轴）。

**选择规则**（`frontend/lib/avatar/avatar-config.ts` 的 `resolveVoiceSource`，有单测）：

```
魔珐渲染中 ? xmov
         : 偏好=server  → server（探测失败也先试一次）
         : 探测到服务端 TTS 就绪 ? server : none（静默）
```

**浏览器原生 TTS（Web Speech API）已整体移除**：系统音色与角色对不上，
默认开着只会让陪伴体验失真。规则简化为「**只有接入 TTS 才出声**」。

用户可在「数字人设置 → 语音引擎」选两档（自动 / 强制服务端），音色另有下拉框。

### 前端契约：`timeline` 与 `useLipSyncTimeline`

```ts
interface AvatarController {
  provider: "none" | "xmov" | "server";   // none = 未接入 TTS，不出声
  /** 与**正在播放的音频同源**的口型时间轴（仅服务端 TTS 提供） */
  timeline?: readonly VisemeFrame[] | null;
  speak(text, ssml?, context?): Promise<void>;   // context = 本轮情绪
}
```

- **服务端 TTS 路径**：音频与 viseme 来自**同一次** `POST /media/avatar`，
  口型时钟对齐 `playing` 事件（真实出声那一刻），字幕用 `audio.currentTime` 推进；
  此路径下 `useLipSyncTimeline` **保持 inactive**，不再多打一次请求
  （否则就是「声音 A + 口型 B」）；
- **魔珐路径**：没有本地音频时长可用，只能另发一次请求让后端按文本估算，
  只求「嘴会动」；
- **降级是逐句的**：某一轮拿不到 `audio_url`（服务挂了/没配）就那一句**静音**，
  不需要重启也不需要重新配置——但也不会换系统音色。

### 音色（服务端 TTS）

GPT-SoVITS 是零样本音色克隆，**音色 = 一段参考音频 + 该音频对应的文字**，没有服务端预置 id。
所以「有哪些声音可选」由后端音色表 `backend/data/tts_voices.json` 定义（属本地数据，不入库）：

```json
{ "gentle": { "ref_audio_path": "refs/gentle.wav", "prompt_text": "今天也辛苦了。", "label": "温柔" } }
```

- 前端只保存「选了哪个 id」（localStorage），参考音频路径**不下发到浏览器**；
- 音色 id 失效（表改了）时前端自动回落默认音色，不会留下「选了却没反应」的死选项；
- 改动音色表需**重启后端**（前端里已写进提示）；
- 参考音频 **必须 3~10 秒**；`prompt_text` 与音频内容逐字一致时效果最好，**留空也能用**；
- 部署步骤与排查看 [`deployment.md`](deployment.md) 第 6.5 节。

### 声随情变：情绪 → 音色

音色表里可以用**保留键** `_emotion_map` 把后端 8 类情绪标签映射到音色 id
（ `_` 开头的键不会被当作音色）：

```json
{
  "_emotion_map": { "sad": "sad", "tired": "sad", "anxious": "sad",
                    "happy": "joyful", "surprised": "joyful",
                    "calm": "neutral", "neutral": "neutral", "angry": "neutral" },
  "sad":     { "ref_audio_path": "refs/sad.wav",     "label": "爱弥斯·忧伤" },
  "joyful":  { "ref_audio_path": "refs/joyful.wav",  "label": "爱弥斯·开心" },
  "neutral": { "ref_audio_path": "refs/neutral.wav", "label": "爱弥斯·中立" }
}
```

解析优先级（都有单测）：

```
显式 voice  >  情绪映射  >  GPT_SOVITS_DEFAULT_VOICE  >  默认参考音频
```

两个刻意的行为选择：

1. **显式传了未知 voice 时不走情绪映射**，而是固定回落默认音色：
   拼错应当表现为可预测的「没换成」，而不是「接心情换了个声音」——后者难排查；
2. 指向不存在音色的映射在**加载时**就丢弃并记 warning（不是等到播放才失效）。

响应里的 `meta.voice_source` 会告诉你是谁选的声音：
`request`（显式）/ `emotion`（按情绪）/ `default`（默认 id）/ `config`（没走音色表）。
前端无需任何配合：本轮情绪已经随 `speak(text, ssml, context)` 传到后端，
面板上的「不选音色」会自动变成「按情绪选音色」（接口的 `emotion_voices` 字段决定文案）。

### 播报流水线：首句提前 + 字声同步

**为什么不再整段合成**：实测合成耗时随文本长度**近似线性增长**（同一台机器、v2ProPlus 微调模型）：

| 文本长度 | 整段合成耗时 |
|---|---|
| 15 字 | 2.99s |
| 46 字 | 8.75s（冷）/ 4.8~5.4s（热） |
| 96 字 | 8.60s |

即长回复要**干等 8~9 秒**才出声。改法：按句切分 → 逐段合成 → **段就绪即播**（字幕同一切换）
→ 播放期间预取下一段。

实测效果（真实 uvicorn + 真实 GPT-SoVITS）：

| 回复 | 分段 | 旧：整段合成就绪 | 新：首句出声 | 收益 | 段间断档 |
|---|---|---|---|---|---|
| 49 字 | 3 段（19/20/10） | 5.38s | **2.81s** | 提前 2.57s（48%） | 无 |
| 96 字 | 7 段 | 8.60s | **2.54s** | 提前 6.06s（70%） | 无 |

三个参数都有实测依据，改它们前请先看数字：

| 参数 | 值 | 依据 |
|---|---|---|
| 分段字数上限 | `max_chars=20` | 切分只按 `。！？；` 断句（不在逗号处断），20 字让常见的 15~17 字句子自然成段又不触发硬切；更小会在句中硬切（语调不自然），更大则首段变长 |
| 预取深度 | **固定 1 段** | 更深会**拖慢首段**：实测深度 2 时服务端把并发的两个请求一起处理，首段从 1.66s 拖到 4.60s。深度 1 已足够无缝——首段之后每段的播放时长（2~4.5s）都大于下一段的合成耗时（1.5~2.6s） |
| 分段播报开关 | **默认开** | 服务端 TTS 下它就是流水线，没有理由默认关；魔珐 SDK 路径段间有约 400ms 过渡，想整段连贯可关 |

**字幕与语音如何同步**：`useChatSession` 不再预先显示整段字幕，而由 `speakChunks` 的
`onChunk` 在**每段音频就绪那一刻**（`play()` 之前）驱动——所以每句的字与声同时出现。

**逐段降级**：某一段拿不到音频（超时 / 服务未部署）时只有**那一段**静音，
前后段继续走服务端，字幕照常推进。

**未采用的两条路（备查）**：
- `streaming_mode=true`：实测首包 1.47s（比非流式的 2.99s 减半），但要用上它必须让前端
  边下边播，且流式拿不到时长 → 口型对齐退化。属于方案 5，本次未做；
- `sample_steps` 调采样步数：实测 8/16/32 → 3.13/2.80/3.18s，**无差异**，别再折腾。

---

## 九、常见问题

| 现象 | 排查 |
|---|---|
| 报「应用不存在或无法使用」 | 密钥来自**驱动应用**；确认应用已创建并完成配置（角色/音色/表演风格） |
| 数字人不说话 | 检查 `avatar.init()` 是否成功；确认 WebSocket 已连接（`ttsa/session`） |
| 说完一句后第二句没反应 | `speak` 不能连续调用，需先 `interactive_idle`（见第五节） |
| 字幕出现 `kacomfort` 之类乱码 | 前端应使用 `display_text` 字段渲染字幕，而不是自行去标签 |
| 选了服务端 TTS 却完全没有声音 | 每一句都是**独立降级**（失败的那句静音，不会改用系统语音）：看 `GET /media/tts/voices` 的 `note`；再看后端 `media/` 目录下有没有新音频；`POST /media/avatar` 的 `meta.degrade_reason` 会写清原因（连不上 / 超时 / 缺参考音频） |
| 声音出来了但嘴不太合 | GPT-SoVITS 无字级时间戳，对齐是**整段级**的（`meta.align=estimated-scaled`）：先看 `meta.duration_source` 是不是 `estimated`（非 WAV 拿不到时长），必要时改用 `GPT_SOVITS_MEDIA_TYPE=wav` |
| 改了 `tts_voices.json` 但下拉框没变 | 音色表在**启动时**读一次（与 `.env` 一致）→ 重启后端 |
| 声音没有随情绪变化 | 音色表里没配 `_emotion_map`（看 `/media/tts/voices` 的 `emotion_voices`），或本轮显式指定了 voice（看 `meta.voice_source`） |
| 某个音色“选了没反应” | 该音色 id 不在音色表里（`meta.unknown_voice` 会写出来）；若是情绪映射，加载时会因指向不存在音色而被丢弃 |
