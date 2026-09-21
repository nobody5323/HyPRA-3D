# 前端方案：Next.js + 魔珐具身驱动 SDK

> 目标：把后端能力（对话 / 记忆 / 情绪 / 播报指令）变成**可演示的具身交互产品**。
> 覆盖赛题要求：表达层（SSML 播报 / 流式分段 / 字幕 / 形象音色）+ 交互层（多轮对话 / 情绪匹配 / 聆听与打断）
> + 具身状态协同（Listen / Think / Speak / Interrupt）

---

## 一、技术栈

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | **Next.js（App Router）+ TypeScript** | 项目既定技术栈；SSR 便于部署 |
| 样式 | Tailwind CSS | 快速实现情绪化视觉（配色随情绪变化） |
| 数字人 | **魔珐 `xmovAvatar.js`（具身驱动 SDK）** | 赛题指定底座，SDK 内部完成 TTS/口型/表情/动作 |
| 通信 | REST（`/chat`、`/media/speak`） | 后端已就绪，无 WebSocket 需求（播报由 SDK 负责） |
| 状态 | React hooks + 轻量 store（或 Zustand） | 会话状态 + 具身状态机 |

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
| **Idle** | 初始 / 长时间无交互 | `avatar.setState('idle')` | 数字人自然待机 |
| **Listen** | 用户聚焦输入框 / 麦克风激活 | UI 层（输入框高亮 + 呼吸光效） | 「我在听」的提示 |
| **Think** | 已提交，等后端返回 | UI 层（思考指示：三点跳动） | 数字人保持 Interactive_idle |
| **Speak** | 收到 `speak.ssml` | `avatar.speak(ssml, true, true)` | 字幕逐句显示 + 口型/表情/KA 动作 |
| **Interrupt** | 用户点击「打断」/ 发送新消息 | `avatar.interrupt()` | 立即停止播报，回到 Interactive_idle |

**关键约束（SDK 官方要求）**：
1. `speak` **不能连续调用** → 两次播报之间必须经 `interactive_idle` 过渡；
2. 用 `onVoiceStateChange`（`voice_start` / `voice_end`）驱动状态机流转；
3. 页面卸载前调用 `avatar.destroy()`。

---

## 三、目录结构

```
frontend/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                    # 主页面：左侧数字人 + 右侧对话
│   └── globals.css
├── components/
│   ├── AvatarStage.tsx             # 数字人容器（SDK 挂载 + 生命周期 + 错误占位）
│   ├── ChatPanel.tsx               # 消息列表 + 输入框（含打断按钮）
│   ├── SubtitleBar.tsx             # 字幕（消费 speak.display_text）
│   ├── MoodIndicator.tsx           # 情绪指示（emotion.label_zh + 强度）
│   ├── StateBadge.tsx              # 具身状态显示（聆听中/思考中/说话中）
│   └── StyleSwitcher.tsx           # 文风切换（4 种预设，用于 A/B 演示）
├── hooks/
│   ├── useAvatar.ts                # ★ SDK 封装：初始化/说话/打断/重连/销毁
│   ├── useChatSession.ts           # ★ 对话 + 状态机编排
│   └── useMoodTheme.ts             # 情绪 → 页面配色/光效
├── lib/
│   ├── api.ts                      # 后端接口封装（/chat、/media/speak）
│   └── types.ts                    # 与后端响应对齐的类型（ChatResponse 等）
└── public/
    └── avatar-fallback.png         # SDK 不可用时的占位形象
```

---

## 四、核心实现要点

### 4.1 SDK 封装（`hooks/useAvatar.ts`）

```ts
type AvatarState = 'idle' | 'listen' | 'think' | 'speak';

export function useAvatar(containerId: string) {
  const avatarRef = useRef<any>(null);
  const [state, setState] = useState<AvatarState>('idle');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // 1) 引入与实例化
    const avatar = new (window as any).XmovAvatar({
      containerId,
      appId: process.env.NEXT_PUBLIC_XMOV_APP_ID,
      appSecret: process.env.NEXT_PUBLIC_XMOV_APP_SECRET,
      gatewayServer: 'https://nebula-agent.xingyun3d.com/user/v1/ttsa/session',
      hardwareAcceleration: 'prefer-hardware',
    });
    avatar.init().then(() => setReady(true));

    // 2) 语音状态 → 驱动状态机（voice_end 后回到交互待机）
    avatar.onVoiceStateChange = (e: any) => {
      if (e === 'voice_start') setState('speak');
      if (e === 'voice_end') { avatar.setState?.('interactive_idle'); setState('idle'); }
    };

    avatarRef.current = avatar;
    return () => avatar.destroy();      // 3) 卸载清理
  }, [containerId]);

  /** 安全播报：先确保处于交互待机，避免 speak 连续调用 */
  async function speak(ssml: string) {
    if (!ready) return false;
    avatarRef.current?.setState?.('interactive_idle');
    avatarRef.current?.speak(ssml, true, true);
    return true;
  }

  function interrupt() {
    avatarRef.current?.interrupt?.();
    avatarRef.current?.setState?.('interactive_idle');
    setState('idle');
  }

  return { state, ready, setState, speak, interrupt };
}
```

### 4.2 对话编排（`hooks/useChatSession.ts`）

```ts
async function send(text: string) {
  setAvatarState('listen');                    // ① 聆听（提交前）
  setAvatarState('think');                     // ② 思考（等待后端）
  const res = await postChat({ text, session_id: sessionId, style_id: styleId });

  setMessages(m => [...m, { role: 'user', text }, { role: 'assistant', text: res.reply }]);
  setMood(res.emotion);                        // ③ 情绪 → 视觉主题
  await avatar.speak(res.speak.ssml);          // ④ 播报（SSML + KA 动作）
  setSubtitle(res.speak.display_text);         // ⑤ 字幕（务必用 display_text）
}
```

> ⚠️ 字幕必须用 `speak.display_text`（后端已剥离 SSML 标签），
> 自行去标签会残留 `kacomfort` 之类的动作文本。

### 4.3 情绪 → 视觉联动（`hooks/useMoodTheme.ts`）

| 情绪 | 主色 | 光效 | 数字人动作（后端已给 KA） |
|---|---|---|---|
| anxious | 暖橙低饱和 | 缓慢呼吸 | comfort |
| sad | 蓝灰 | 微弱 | comfort |
| happy | 明亮暖黄 | 轻快 | Hello |
| calm | 青灰 | 平稳 | idle |
| tired | 深蓝灰 | 极缓 | slow_down |

### 4.4 稳定性与降级（赛题「稳定性与容错」评分点）

| 场景 | 处理 |
|---|---|
| SDK 脚本加载失败 / `init()` 报错 | 显示占位形象 + 字幕照常工作（纯文本对话仍可用），顶部提示「数字人暂不可用」 |
| WebSocket 断开 | 监听 SDK 错误回调 → 指数退避重连；重连期间降级为字幕模式 |
| 后端不可用 | 输入框禁用 + 「重试」按钮，保留已有消息 |
| 弱网 | 播报前提示「网络较慢」；`/chat` 设置超时与重试 |
| 用户打断 | 立即 `interrupt()`，不等待服务端确认（SDK 原生支持） |

---

## 五、后端接口契约（已就绪）

### `POST /chat`

```ts
type ChatResponse = {
  session_id: string;
  reply: string;
  emotion: { label: string; label_zh: string; intensity: number;
             confidence: number; facial_expression: string; source: string };
  speak: { ssml: string; display_text: string; voice: string;
           ka_action: string; tone: string; intensity: number };
  memory_counts: { memories: number; facts: number };
  style: { style_id: string; style_name: string; examples: number; sampling: object };
  worldbook_hits: string[];
  warnings: string[];
};
```

### `POST /media/speak`（只生成播报指令）

```ts
// 请求
{ text: string; emotion?: string; intensity?: number; voice?: string; streaming?: boolean }
// 响应
{ ssml: string; display_text: string; ka_action: string; tone: string; chunks?: string[] }
```

---

## 六、分期实施计划

| 批次 | 内容 | 验收标准 |
|---|---|---|
| **F1 骨架与打通** | Next.js 项目 + 对话 UI + 调 `/chat` + 字幕显示（**暂不接 SDK**） | 能在页面完成一轮多轮对话，字幕/情绪正确显示 |
| **F2 SDK 集成** | 引入 `xmovAvatar.js`，数字人出现并能播报 | 数字人说出回复（口型/表情/动作由 SDK 完成） |
| **F3 状态机** | Listen / Think / Speak / Interrupt 全流程 + 情绪视觉联动 | 状态徽标随流程变化；可打断播报 |
| **F4 稳定性** | 断线重连、SDK 降级、错误提示、弱网提示 | 拔网线可演示降级；重连后恢复 |
| **F5 演示包装** | 文风切换（A/B）、情绪曲线、视觉打磨、演示脚本对齐 | 与 3–5 分钟演示视频脚本一致 |

**合并进 docker compose**（评审一键部署）：✅ **已完成** —— `frontend` 服务已并入根目录
`docker-compose.yml`（三阶段 Dockerfile + Next.js **standalone** 产物，端口 3000；
`NEXT_PUBLIC_API_BASE` 经 `build.args` 构建期内联为宿主机可达地址；
健康依赖链 `qdrant → backend(healthy) → frontend`，详见 [deployment.md](deployment.md)）。

---

## 七、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 魔珐积分/额度不足 | 无法真实演示 | 用赛题邀请码领 1000 积分；演示前预留额度 |
| `appSecret` 暴露在前端 | 安全（赛题未强制要求） | 演示环境限定域名；文档说明生产应改为后端签发临时凭证 |
| SDK 版本/浏览器兼容 | 渲染异常 | 锁定 SDK 版本；演示用 Chrome；准备占位降级 |
| 前端工期挤压演示视频 | 交付风险 | 先保证 F1+F2（可演示的最小闭环），F3–F5 按时间取舍 |
