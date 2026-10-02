# 前端方案：Next.js + 魔珐具身驱动 SDK

> 目标：把后端能力（对话 / 记忆 / 情绪 / 播报指令）变成**可演示的具身交互产品**。
> 覆盖赛题要求：表达层（SSML 播报 / 流式分段 / 字幕 / 形象音色）+ 交互层（多轮对话 / 情绪匹配 / 聆听与打断）
> + 具身状态协同（Listen / Think / Speak / Interrupt）
>
> **本文档描述的是当前实现状态**（前端已对接后端全部公开接口），
> 代码位置以 `frontend/` 下的真实文件为准。

---

## 一、技术栈

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | **Next.js 15（App Router）+ TypeScript** | 项目既定技术栈；`output: standalone` 供 Docker 三阶段构建 |
| 样式 | Tailwind CSS（语义色板集中在 `tailwind.config.ts`） | 快速实现情绪化视觉（配色随情绪变化） |
| 数字人 | **魔珐 `xmovAvatar.js`（具身驱动 SDK）** | 赛题指定底座，SDK 内部完成 TTS/口型/表情/动作 |
| 通信 | REST：`/chat`、`/chat/presets|personas|styles`、`/media/speak`、`/knowledge/*`、`/health` | 后端已就绪，无 WebSocket 需求（播报由 SDK 负责） |
| 状态 | React hooks（`useAvatar` / `useChatSession`）+ `useAvatarCredentials` | 无全局 store：会话与具身状态集中在 hook 内，避免多源订阅导致的状态不一致 |
| 质量门禁 | ESLint 9（`next/core-web-vitals`，`--max-warnings=0`）+ vitest（jsdom） | 静态检查与回归测试都进 CI；测试着重锁住「靠手测难以复现」的时序逻辑 |

前端**不引入**任何运行时依赖（无 UI 库、无状态库、无 HTTP 客户端）：全部为 React + 浏览器原生 API。

---

## 二、具身状态机（赛题明确的评分点）

```
                 用户输入 / 麦克风激活
        ┌──────────────────────────────────┐
        ↓                                  │
    ┌───────┐    提交     ┌────────┐   回复  ┌───────┐
    │ Idle  │ ─────────▶ │ Listen │ ──────▶ │ Think │
    │ 待机  │            │ 聆听   │         │ 思考  │
    └───────┘            └────────┘         └───────┘
        ↑                                       │
        │  播报结束(voice_end)                    ↓ speak(ssml)
        │                                    ┌───────┐
        └────────────────────────────────────│ Speak │
                                             │ 播报  │
                                             └───────┘
                                                 │ 用户打断
                                                 ↓
                                            ┌───────────┐
                                            │ Interrupt │ → 回到 Interactive_idle
                                            └───────────┘
```

| 状态 | 触发 | SDK / UI 动作 | 视觉表现 |
|---|---|---|---|
| **Idle** | 初始 / 播报结束 | `avatar.idle()` | 数字人自然待机 |
| **Listen** | 提交前 | `avatar.listen()` | 输入框高亮 + 「我在听」 |
| **Think** | 已提交，等后端返回 | `avatar.interactiveidle()` | 思考指示：三点跳动 |
| **Speak** | 收到 `speak.ssml` | `avatar.speak(ssml, true, true)` | 字幕逐句显示 + 口型/表情/KA 动作 |
| **Interrupt** | 用户点击「打断」/ 发送新消息 | `avatar.interrupt()` + `avatar.interactiveidle()` | 立即停止播报，回到交互待机 |

**关键约束（SDK 官方要求，实现中已处理）**：

1. `speak` **不能连续调用** → 两次播报之间必须经 `interactive_idle` 过渡；
   实现见 `useAvatar.ts` 的 `STATE_SWITCH_DELAY_MS`（状态切换经 WebSocket 下发，
   立即 `speak` 会被丢弃）。
2. 用 `onVoiceStateChange`（`voice_start` / `voice_end`）驱动状态机流转；
3. 页面卸载前调用 `avatar.destroy()`（否则泄漏 WebGL 资源）。
4. SDK 公开方法为**小写无下划线**形式：`idle()` / `listen()` / `interactiveidle()`
   / `interrupt()`（不是 `setState('interactive_idle')`——早期文档示例有误，已在实现中纠正）。

---

## 三、目录结构

```
frontend/
├── app/
│   ├── layout.tsx                  # 根布局（魔珐域名 preconnect + 主题色）
│   ├── page.tsx                    # 主页面：左侧数字人舞台 + 右侧状态/设置/对话
│   └── globals.css                 # 语义色板 / 焦点环 / 减少动效适配
├── components/                     # 按域分组
│   ├── avatar/                     # AvatarStage / AvatarSettings / Live2DStage / LocalAvatarStage
│   │                               #   / StaticPortraitStage / ModelLibraryPanel / ModelSourcePanel / StateBadge
│   ├── chat/                       # ChatPanel / SubtitleBar / MoodIndicator / MemoryTrace / SessionList
│   │                               #   / MicButton / ImageButton / CapabilityHint
│   ├── settings/                   # PersonaSwitcher / StyleSwitcher / PresetSwitcher / JailbreakSwitcher
│   │                               #   / ModeSwitcher / LlmSettings / AgentBadge / PluginCenter(+Detail/DevPanel)
│   │                               #   / SkillPanel / SchemaForm / TavernImportPanel / useAnchoredPopup
│   └── studio/                     # StudioPanel / PersonaStudio / WorldBookStudio / StyleStudio / KnowledgePanel
│                                   #   / StPresetPanel(+Editor/AdaptPanel) / StudioBadge
├── hooks/                          # 按域分组
│   ├── avatar/                     # useAvatar（SDK 封装 + 三实现 + 逐句降级）/ useAvatarCredentials
│   │                               #   / useAvatarModels / useTtsPreferences / useLipSyncTimeline
│   ├── chat/                       # useChatSession（对话 + 状态机编排 + 分段播报 + 主动消息订阅）
│   │                               #   / usePersonaCatalog
│   └── perception/                 # useSpeechInput（语音）/ useImageShare（发图）/ usePerceptionCapabilities
├── lib/                            # 按域分组
│   ├── api/                        # client.ts（统一出口 + ApiError）/ types.ts（唯一契约来源）/ events.ts（SSE）
│   ├── avatar/                     # avatar-config / avatar-renderer / static-portrait-renderer
│   │                               #   / avatar-model-source / lipsync / portrait-assets
│   ├── chat/                       # session-store / persona / user / mode / jailbreak / shortcut
│   ├── live2d/                     # 三态桥（cubism-bridge / live2d-renderer / model-assets / config）—— 路径不可动
│   └── studio/                     # st-preset.ts（预设字段元信息 / 顺序合并 / 补丁深合并）
├── vendor/cubism/                  # Cubism SDK（脚本落位，不入库）
├── tests/                          # vitest：接口契约 / 分段播报守卫 / 退避重连 / 各面板
├── eslint.config.mjs               # ESLint 9 扁平配置（next/core-web-vitals，--max-warnings=0）
├── tailwind.config.ts              # 语义色板（mood.* 为情绪色唯一来源）
└── vitest.config.mts               # vitest 配置（jsdom + `@` 别名）
```

> 目录按**域**分组（对齐 `AGENTS.md` §3）；`lib/live2d/` 为三态桥，路径不可动。

---

## 四、核心实现要点

### 4.1 SDK 封装（`hooks/avatar/useAvatar.ts`）

对外只暴露一个与实现无关的控制器接口（`AvatarController`），
魔珐 SDK / 服务端 TTS / 静默是它的三个实现——页面按凭证与探测结果**在渲染期直接选择**，
因此不会先渲染一帧错误状态：

```ts
interface AvatarController {
  state: AvatarState;                       // idle | listen | think | speak
  stateLabel: string;
  ready: boolean;
  provider: "browser" | "xmov";
  stage: AvatarInitStage;                   // unconfigured|loading-sdk|initializing|reconnecting|ready|failed
  detail: string;                           // 阶段详情 / 失败原因（现场排查用）

  setState: (state: AvatarState) => void;
  /** 整段播报：text 给服务端 TTS，ssml 给魔珐 SDK */
  speak: (text: string, ssml?: string) => Promise<void>;
  /** 分段播报：逐段 { text, ssml }，段间自动做 interactive_idle 过渡 */
  speakChunks: (chunks: SpeechChunk[], onChunk?: (index: number) => void) => Promise<void>;
  interrupt: () => void;
}
```

关键实现细节：

- `speak()` **等待 `voice_end` 才 resolve**（带按文本长度估算的超时兜底），
  与另两个实现保持同一语义；否则调用方会立刻把「说话中」打回 idle；
- ⚠️ **语音事件的坑**：`onVoiceStateChange(state, duration, client_speak_id)` 的 `state`
  **实际取值是 `"start"` / `"end"`**（文档用事件名 `voice_start` / `voice_end` 描述）。
  只认后者时事件永远匹配不上，每段播报都退化成靠超时收尾，而最后一段会被紧接着的
  `idle()`（内部 `interrupt` 渲染调度）切断——实测现象就是「最后一句说不完」。
  实现里两种写法都接受，并有回归用例锁住（`tests/useAvatar.xmov.test.ts`）；
  超时也相应放宽（每字 400ms / 最小 12s）并在收尾留 600ms 尾音缓冲；
- 降级链路：未配置凭证 / SDK 脚本加载超时（20s）/ `init()` 超时（90s）/ `onError` 重连用尽
  → 调用 `onUnavailable(reason)`，页面切到本地渲染 + 静默，并**保留失败原因**；
- 断线自动重连：`onError` 按指数退避（1s → 2s → 4s，最多 3 次）重建 SDK，
  期间 `stage = "reconnecting"`；用尽后才降级。重新连上后重连计数归零。

### 4.2 对话编排（`hooks/chat/useChatSession.ts`）

```ts
// ① 聆听 → ② 思考（等后端）→ ③ 播报（含字幕）→ ④ 回待机
setState("listen");
const res = await postChat({ text, session_id, persona_id, style_id, user_name, preset_id });

setEmotion(res.emotion);            // 情绪 → 视觉主题
setMemoryCounts(res.memory_counts); // 记忆/行动证据 → MemoryTrace
setState("speak");
if (streamingSpeech) {              // 可选：分段播报（默认关闭）
  const chunks = await fetchSpeechChunks(res.reply, res.emotion);  // POST /media/speak
  chunks.length > 1
    ? await speakChunks(chunks, (i) => setSubtitle(chunks[i].text))  // 字幕随段推进
    : await speak(res.speak.display_text, res.speak.ssml);           // 回退整段
} else {
  await speak(res.speak.display_text, res.speak.ssml);
}
setState("idle");
```

> ⚠️ 字幕必须用 `speak.display_text`（后端已剥离 SSML 标签）。
> 分段播报时用 `/media/speak` 返回的 `chunks`（纯文本），
> **SSML 一律由后端生成**（`ssml_chunks`）——前端自行拼标签会漏掉 XML 转义与 KA 事件结构。

**打断的稳定性设计**（`turnRef` 回合序号）：发送与打断都会自增回合号，
旧回合的后续写入（状态、字幕、错误）据此全部失效。
否则打断后旧回合会把新回合状态打回 idle，且第二次「打断」会静默失效。

**人设切换 = 记忆命名空间切换**：`persona_id` 同时是后端的 `companion_id`，
切换时清空会话、消息、情绪与记忆计数（不复用旧 `session_id`），
否则会把上一个角色的历史与召回带进新角色。

### 4.3 情绪 → 视觉联动

情绪色板集中在 `tailwind.config.ts` 的 `mood.*`（唯一来源），
由 `MoodIndicator` 的强度条与 `AvatarStage` 的情绪光晕共用；
数值动作（KA）由后端按情绪与强度阈值决定（见 `backend/app/digital_human/ssml.py`）。

| 情绪 | 主色 | 光效 | 数字人动作（后端已给 KA） |
|---|---|---|---|
| anxious | 暖橙低饱和 | 缓慢呼吸 | comfort |
| sad | 蓝灰 | 微弱 | comfort |
| happy | 明亮暖黄 | 轻快 | Hello |
| calm | 青灰 | 平稳 | idle |
| tired | 深蓝灰 | 极缓 | slow_down |

### 4.4 稳定性与降级（赛题「稳定性与容错」评分点）

| 场景 | 处理 | 状态 |
|---|---|---|
| SDK 脚本加载失败 / `init()` 报错 | 显示占位形象 + 字幕照常工作（纯文本对话仍可用），顶部提示「数字人暂不可用」 | ✅ 已实现 |
| 初始化卡住 | 脚本加载 20s / `init()` 90s 超时兜底，避免永久停在「初始化中」 | ✅ 已实现 |
| WebSocket 断开 | `onError` → 指数退避重连（最多 3 次），期间降级为字幕 + 状态提示 | ✅ 已实现 |
| 重连用尽 | 降级为静默（不再出声），保留失败原因与排查建议 | ✅ 已实现 |
| 后端不可用 | 顶部「后端未连接」徽标 + 每 5s 自动重试；错误条 `role="alert"` 提示，保留已有消息 | ✅ 已实现 |
| 用户打断 | 立即 `interrupt()`，不等待服务端确认 | ✅ 已实现 |
| 弱网 | 播报前无提示（当前未做「网络较慢」探测） | ⬜ 未实现（优先级低） |

---

## 五、后端接口契约（已全部对接）

所有类型定义集中在 `frontend/lib/api/types.ts`；`lib/api/client.ts` 统一出口，
非 2xx 一律抛 `ApiError(status, detail)`（知识库 409 判重需要读 `detail` 结构体）。

### `POST /chat` — 一轮完整对话

```ts
type ChatResponse = {
  session_id: string;
  persona_id: string;
  reply: string;
  emotion: EmotionInfo | null;        // 后端无情绪时返回 {}，前端规范化为 null
  system_prompt: string;              // 仅在 /docs 调试用，界面不展示
  messages: { role: string; content: string }[];
  worldbook_hits: string[];           // 命中的世界书条目 id → MemoryTrace
  skipped: string[];                  // 因 token 预算被跳过的条目 id
  memory_counts: { memories?: number; facts?: number };  // 情景记忆 / 语义事实
  knowledge_hits: number;             // 个人记忆（知识库）召回条数
  memory_scheduled: boolean;          // 记忆写入已提交后台（响应时尚未落库）
  warnings: string[];
  estimated_tokens: number;
  style: { style_id; style_name; examples; sampling; profile };   // → PresetSwitcher/文风展示
  preset: { preset_id; preset_label; temperature; ... };          // 本轮实际生效的模型预设
  speak: SpeakCommand | null;         // SSML + 字幕 + 音色（后端 avatar 未启用时为空）
  tools_used: { name: string; arguments: string; result: string }[];  // → ChatPanel 工具提示
  note: string;                       // 编排方式与运行模式说明
};
```

### `GET /chat/presets` / `GET /chat/personas` / `GET /chat/styles`

界面三个选择器的数据源（清单均由后端预设文件决定，前端不硬编码）：

```ts
// presets  模型适配档：{ model, auto_preset_id, presets: PresetInfo[] }
// personas 陪伴对象：  { default_persona_id, personas: { id, name, title, description, tags }[] }
// styles   文风：      { default_style_id, styles: { id, name, description, tags, examples }[] }
```

> 页面标题里的角色名也取自 `personas`（不再硬编码），
> 「陪伴对象」选择器仅在清单 ≥ 2 项时渲染。

### `POST /media/speak` — 播报指令（分段播报用）

```ts
// 请求：{ text, emotion?, intensity?, voice?, streaming?, max_chars? }
// 响应：{ ssml, display_text, voice, emotion, ka_action, tone, intensity,
//         chunks?: string[],        // 纯文本分段 → 字幕按段推进
//         ssml_chunks?: string[] }  // 与 chunks 一一对应的 SSML → 逐段喂 SDK
//
// 注：`chunks` 已滤掉**纯空白段**（分句正则会把换行单独切出来）——
// 空段的 SSML 会退化为 `<speak></speak>`，撞上首段时 KA 动作会丢失。
// 因此「把 chunks 拼回去 == 原始文本」并不成立，字幕只按段推进即可。
```

### `POST /knowledge/upload` / `GET /knowledge/list` / `DELETE /knowledge/{doc_id}` — 个人记忆

```ts
// upload：multipart（file 或 text）+ query companion_id + form title/force
//         判重命中且未带 force → 409，detail 为判重详情（界面弹「覆盖/取消」）
// list：  ?companion_id= → DocumentInfo[]
// delete：/{doc_id}?companion_id= → { doc_id, removed_chunks }
```

`companion_id` 一律取当前 `persona_id`：切换陪伴对象时列表整体重载，
不会把 A 角色的私人资料显示在 B 角色下。

### `GET /chat/sessions` / `GET /chat/sessions/{id}/history` — 会话历史

```ts
// 列表：?persona_id=&limit= → { session_id, persona_id, title, message_count, updated_at }[]
// 历史：/{session_id}/history    → { session_id, persona_id, user_name, messages[] }
```

会话由**后端持久化**（SQLite，`chat_sessions` / `chat_turns` 两表，与冷层同库不同表），
前端只在 localStorage 记「当前会话 id」。这是刻意的不对称：

- 消息的唯一事实来源是后端 → 界面与**模型上下文**永远一致；
  前端若自行缓存消息，会出现「界面显示 10 轮、模型只看得到 1 轮」的割裂（演示翻车点）；
- 原始留档与热层窗口分开：数据库保留全部消息（界面能看到完整对话），
  而喂给模型的仍只是最近 `max_history_turns`（20）条。

刷新后前端用该 id 拉历史恢复界面；后端重启也丢不了（已落库）。
切换陪伴对象时按各自的指针分开恢复（会话按 `persona_id` 隔离）。

### `GET /health` — 健康检查 + 行动层状态

```ts
{ status, app, mcp: { name, transport, connected, tools: string[], error }[] }
```

`mcp` 供 `AgentBadge` 展示「行动层 N/M 台 · K 个工具」，展开可见各服务器与工具清单。

### 未接入的两个接口（有意保留）

| 接口 | 为什么不接 |
|---|---|
| `POST /media/avatar` | 产出的是**渲染无关的驱动时间轴**（口型/表情/动作 + 音频），用于接入自研 3D/2D 渲染；当前演示走魔珐 SDK 实时驱动，SDK 内部已完成 TTS 与口型对齐 |
| `GET /media/audio/{filename}` | 同上：音频由 SDK 直接播放，不经前端 |

两者是「可降级接入任意模型」的能力证明，保留在后端并已有测试覆盖
（`backend/tests/test_digital_human/test_avatar.py`）。

---

## 六、分期实施计划

| 批次 | 内容 | 状态 |
|---|---|---|
| **F1 骨架与打通** | Next.js 项目 + 对话 UI + 调 `/chat` + 字幕显示（暂不接 SDK） | ✅ 完成 |
| **F2 SDK 集成** | 引入 `xmovAvatar.js`，数字人出现并能播报 | ✅ 完成 |
| **F3 状态机** | Listen / Think / Speak / Interrupt 全流程 + 情绪视觉联动 | ✅ 完成 |
| **F4 稳定性** | 断线重连、SDK 降级、错误提示 | ✅ 完成（弱网探测未做） |
| **F5 演示包装** | 文风切换（A/B）、情绪曲线、视觉打磨 | ✅ 文风与模型档已完成 |
| **F6 接口对齐**（本轮） | 个人记忆上传、人设/文风清单后端化、记忆与行动可见性、分段播报 | ✅ 完成 |
| **F7 演示脚本对齐** | 与 3–5 分钟演示视频脚本逐幕对齐（见 `docs/` 演示脚本） | ⬜ 待做 |
| **F8 会话持久化**（本轮） | 会话落 SQLite + 历史列表 / 新建对话 / 打开历史会话 + 草稿持久化 | ✅ 完成 |

**合并进 docker compose**（评审一键部署）：✅ **已完成** —— `frontend` 服务已并入根目录
`docker-compose.yml`（三阶段 Dockerfile + Next.js **standalone** 产物，端口 3000；
`NEXT_PUBLIC_API_BASE` 经 `build.args` 构建期内联为宿主机可达地址；
健康依赖链 `qdrant → backend(healthy) → frontend`，详见 [deployment.md](deployment.md)）。

CI 已覆盖：后端 `pytest` + 仓库卫生 + 两端镜像构建 + 前端 **`lint`（0 warning）/ `typecheck` / `vitest` / `build`**。

前端校验命令与测试覆盖范围见 [`frontend/README.md`](../frontend/README.md) 的「开发校验」一节；
其中「分段播报的循环控制」与「退避重连」已有回归用例（靠可控替身控制
「一段念完」与「被打断」的时序——这两种情况在真实环境里都表现为回到 idle）。

---

## 七、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 魔珐积分/额度不足 | 无法真实演示 | 用赛题邀请码领 1000 积分；演示前预留额度 |
| `appSecret` 暴露在前端 | 安全（赛题未强制要求） | 演示环境限定域名；文档说明生产应改为后端签发临时凭证 |
| SDK 版本/浏览器兼容 | 渲染异常 | SDK 走官方 `@latest`；演示用 Chrome；已有占位降级 |
| **分段播报的段间停顿** | 听感不如整段连贯 | 默认**关闭**，界面开关控制；需真机调校 `max_chars` 与过渡时长 |
| 前端工期挤压演示视频 | 交付风险 | F1–F6 已完成，重心转向 F7 演示脚本与录制 |

---

## 八、界面上的「可解释性」设计

赛题评分点里「记忆与个性化」「具身智能协同」的难点在于**评审看不见**。
因此前端把后端如实返回的过程数据摊开显示（数据全部来自 `/chat` 与 `/health`，无前端伪造）：

| 界面元素 | 展示内容 | 对应的后端能力 |
|---|---|---|
| `MemoryTrace` | 本轮命中世界书条目 / 个人记忆条数 / 情景记忆条数 / 语义事实条数 / 是否已后台写入 / 提示词 token | 四层记忆召回 + PromptManager 预算 + 异步写入 |
| `KnowledgePanel` | 已入库文档（来源类型/片段数/时间）、清洗比、MinHash 判重详情 | 个人记忆的解析→清洗→判重→分块→入库全链路 |
| `MoodIndicator` | 情绪标签 + 强度 + 语气 + 表情键 + 识别来源（模型/规则兜底） | function calling 情绪结构化输出 |
| `AgentBadge` | MCP 服务器连接数、工具数、各服务器工具清单与错误 | Agent 行动层 / MCP 协议接入 |
| `ChatPanel` 工具提示 | 「已记录这次心情」「已调用外部服务<服务器>」 | function calling + MCP 工具的调用结果 |
| `PresetSwitcher` | 本轮实际生效的模型适配档与采样参数 | 模型适配分层（推理模型思考开关等） |
