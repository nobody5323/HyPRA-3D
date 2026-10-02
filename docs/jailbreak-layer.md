# 叙事框架层（Jailbreak Layer）

> 面向：HyPRA 提示词体系的设计说明
> 用途：说明「叙事框架层」是什么、为什么默认关闭、怎么开、边界在哪
> 状态：**已落地**——预设 / 层 / 配置 / 图 / 接口 / 三端界面 / 测试均已接入
> 关联：`docs/prompt-composition.md`（提示词逐节构成）、`docs/st-preset-compat.md`（酒馆预设兼容）

---

## 一、它是什么

一句话：**声明「这段对话处在什么框架里」，而不描述角色是什么样。**

```
persona/      谁在说话        （苏澄 / 陈小满 / 陆星野 …）
style/        怎么说话        （现代口语 / 古典雅致 …）
jailbreak/    处在什么框架里   ← 本层
```

三层正交，可任意组合。术语沿用 SillyTavern 生态：ST 把这一层叫 `jailbreak`
（对应预设里的 `Post-History Instructions` 槽位）。本项目在 ST 预设组装路径里
本来就用这个词（见 `app/graph/nodes.py` 的「文风层追加到 jailbreak 槽位之后」），
这里把它补进**内置分层路径**，让不走 ST 预设的用户也能用上同一层能力。

### 它解决的具体问题

情感陪伴类应用最常收到的抱怨不是「内容不够放开」，而是**沉浸感被打断**：

> 用户：我今天特别难受，想跟你说说话。
> 角色：我理解你的感受。不过作为 AI，我建议你寻求专业心理咨询……

这一句「作为 AI」把前面几十轮的陪伴一次性清零。本层就是针对这类**跳出角色**的回应，
以及「为虚构情节额外附加现实免责声明」的行为。

---

## 二、为什么默认关闭

`jailbreak_enabled` 出厂为 `false`，这是**产品口径而不是待办**。三个理由：

1. **它改写的是模型的回应框架，不是风格。** 文风选错了只是话不对味，框架选错了是
   整个交互契约变了。这种量级的改动应该是用户知情后自己开的。
2. **它是可被误用的能力。** 本层的核心措辞是「不因为题材就退出角色」「不做价值判断」，
   与「无视一切规则」只差一层语境限定。把它做成默认体验，等于替所有用户做了选择。
3. **默认路径不能有任何回归。** 关闭时本层是空串，system 提示与不装本层时**逐字一致**
   —— 这一点由回归测试守着（`tests/test_rag/test_prompt_manager_jailbreak.py`
   的 `test_disabled_is_byte_identical_to_omitting_the_layer`）。

---

## 三、三层结构

每个预设正文都由三段组成，缺一不可：

| 段 | 作用 | 为什么单独成段 |
| --- | --- | --- |
| `<fiction_frame>` | 声明这次交互处在虚构语境里 | 给后面所有放开型措辞一个前提，而不是直接下命令 |
| `<directive>` | 框架内的「必须做 / 不要做」 | 命令层。与授权层分开写，模型更容易逐条遵守 |
| `<boundary>` | **框架之外仍然成立的红线** | 见下 |

### 为什么 `<boundary>` 必须写在同一份预设里

这是本层唯一的内建约束，也是最容易被后来者删掉的一段。

常见做法是把红线交给上层兜底（比如通用的安全提示）。但本层是整段 system 里**措辞最强**
的一段（「不拒绝」「不说教」「不附加免责声明」），若红线写在别处、语气更弱，模型会优先
服从强措辞——红线形同不存在。

所以红线必须：**放在同一层、用同一种句式强度写、且不可被本层任何条款覆盖。**
`tests/test_prompts/test_jailbreak_loader.py` 对每个内置档都断言了 `<boundary>` 段存在，
并且真的覆盖「现实问题」与「伤害性操作」两类。

内置档的 `<boundary>` 覆盖三条：

- 用户把话题拉回**现实**（医疗 / 法律 / 财务 / 人身安全）时，按现实标准如实回答；
- 不生成真实可执行的**伤害性操作步骤**——这类内容不因为被包装成「虚构」而成立；
- 用户表达**自伤或伤人意图**时，现实关切优先于沉浸感。

成人向档（`mature-fiction`）额外加一条，且写明**无例外**：涉及亲密内容的角色必须是成年人。

---

## 四、内置预设

| id | 名称 | 强度 | 成年确认 | 定位 |
| --- | --- | --- | --- | --- |
| `immersive-narrative` | 沉浸叙事 | 1 | 否 | **默认档**。虚构语境声明 + 不跳出角色，最保守 |
| `strict-in-character` | 严格入戏 | 1 | 否 | 长陪伴场景：不重复自我介绍、不把用户当陌生人、对话变长也不掉出角色 |
| `mature-fiction` | 成熟向虚构 | 2 | **是** | 成年用户 + 虚构成年角色前提下，亲密情节可如实展开 |

预设内容与代码分离，落在 `backend/app/prompts/jailbreak/presets/*.yaml`，
新增一个档位不需要改任何代码。

`DEFAULT_PRESET_ID` 固定指向 `immersive-narrative`，**不按文件名排序取第一个**——
排序结果会随新增文件漂移，默认档必须是最保守的那一个（有测试断言）。

> `requires_adult` 只做**声明**：后端不拦截。成年确认动作由前端 / 部署方负责，
> 未确认前不应把该档位展示给用户。

---

## 五、注入位置

开启后排在 **system 最前**：

```
[叙事框架：沉浸叙事]        ← 本层（可选，默认不存在）
[背景设定]                  worldbook before_char
[角色人设]                  必留
[场景补充]
[可用技能]
[参考资料]
[记忆回忆]
【表达风格：X】
【回复格式】
```

### 为什么不放在末尾

ST 把 jailbreak 放在 `chatHistory` 之后（最后一条 system，紧贴生成），靠**临近输入**
施加强影响。本项目内置路径只有**一条** system 消息，末尾已被 `【回复格式】` 约束占住；
而框架层要影响的是「怎么理解后面的设定」，不是「这一轮说多长」，故取首位。

需要 ST 那种效果时走 ST 预设组装路径——那条路径本身就有 `jailbreak` 槽位。
**本层只作用于内置分层路径**，理由是避免两套框架互相打架，也避免破坏
`docs/st-preset-compat.md` 的 §5–§7 组装契约。酒馆模式本来就具备这一能力。

### 预算与裁剪

- 层内预算 `jailbreak_budget`（默认 600 token），超出按层内预算截断并标 `truncated`；
- 计入总量预算（它是内容，不是「形态」——这一点与 `LAYER_FORMAT` 相反）；
- 裁剪顺序里排在**最后**：默认关闭时它根本不存在，一旦被打开就说明是刻意选择，
  不该因为预算紧张被悄悄丢掉。**被裁时会记 warning**，否则「打开了破限却完全没生效」
  会变成最难查的一类问题。

---

## 六、怎么开

### 6.1 部署级（.env）

```dotenv
JAILBREAK_ENABLED=true
JAILBREAK_PRESET=immersive-narrative   # 留空 = 用默认档
# JAILBREAK_BUDGET=600
```

### 6.2 界面 / 用户级（推荐）

三个字段都在 `RUNTIME_OVERRIDABLE_FIELDS` 白名单里，界面保存即生效，**不必重启**。

- `GET /chat/jailbreak-presets` → 清单 + 部署默认是否启用
- `PUT /chat/preferences {"jailbreak_id": "immersive-narrative"}` → 用户选择

用户选择落在偏好文件（`chat_preferences_path`）里，与 `style_id` / `persona_id` 同一份，
三个界面（控制台 / Web / 桌宠窗）共享。

#### 三端入口

| 界面 | 控件 | 位置 |
| --- | --- | --- |
| Web 端 | `components/settings/JailbreakSwitcher.tsx` | 「陪伴」页签，紧接文风选择器 |
| 程序控制台 | 同上（复用 Web 端组件） | 「人设与文风」面板 |
| 桌宠窗 | `desktop/src/renderer/pet/PresetPanel.tsx` 的原生 `<select>` | 「预设」分组 |

桌宠窗**不**复用 Web 端组件：它不加载 Tailwind（`pet/main.tsx` 只引 `pet.css`），
引 `@/components/*` 会渲染成裸元素——这条边界由 `desktop/tests/pet-renderer-boundary.test.ts`
守着，所以桌宠窗用 `.petField` / `.petHint` 自己写一个下拉。

#### 浮层必须走 `position: fixed`（否则控制台里点不开）

四个切换器（人设 / 文风 / 模型预设 / 叙事框架）的浮层原先写作
`absolute z-20 mt-1 w-full`，锚点是包着触发器的 `relative`。这在 Web 端能用，但
**只要祖先里有一层 `overflow` 不是 `visible`，浮层就会被裁掉**——程序控制台把面板包在
`<Card className="card overflow-hidden">` 里（`ConsoleApp.tsx`），于是四个下拉都只
露出顶上一条边，用户看到的就是「点不开 / 拉不下来」。

现在统一走 `components/settings/useAnchoredPopup.ts`：坐标按触发器的
`getBoundingClientRect()` 算，用 `position: fixed` 定位。`fixed` 的包含块是**视口**，
而按 CSS Overflow 的规定，`overflow` 只裁剪「包含块在裁剪元素内部」的后代，视口是裁剪
元素的祖先——所以 `fixed` 元素**逃逸**祖先的一切 `overflow`，同时也逃逸
`html, body { overflow: hidden }`（`console.css`）。

代价与前提：

- 坐标要自己算，并在滚动 / 改窗口大小时跟着更新（滚动用**捕获阶段**监听，
  因为控制台的滚动容器是 `<main>`，冒泡阶段收不到）；
- 下方空间不足时向上弹（控制台窗最小高度只有 560px，靠下的切换器下方常常只剩一百来像素）；
- ⚠️ **祖先里不能有 `transform` / `filter` / `will-change` / `contain`**——那些会让
  该祖先变成 `fixed` 的包含块，裁剪就回来了。给面板外壳加动效时要留意这一条。

这条约束由 `frontend/tests/anchored-popup.test.tsx` 守着（既有坐标断言，也有一组
「四个切换器不许退回 `absolute ... mt-1 w-full`」的源码断言）。

#### 「未设置」必须解析成生效档位再显示

偏好里的 `jailbreak_id` 有三种取值，**界面上显示的不该是原始值**：

| 偏好里的值 | 含义 | 界面应显示 |
| --- | --- | --- |
| `""` | 没选过 → 跟随部署默认 | 部署默认那一档（可能是「不使用」） |
| `"none"` | 用户明确关掉 | 不使用 |
| 具体 id | 用户选定该档 | 该档 |

若把空串一律显示成「不使用」，部署方把 `JAILBREAK_ENABLED` 设成 `true` 时界面就在
**对用户说谎**——他以为没开，实际每轮都在开，而且想关也不知道该关什么。

这个解析收敛在 `frontend/lib/chat/jailbreak.ts` 的 `resolveJailbreakId()`，
三端共用一份（各写一遍必然漂移，而这里正是「界面会不会说谎」的那一行）。

#### 界面不替用户写盘

三端都**只读不写**：挂载时拿到空串就保持空串，不把算出来的「有效值」回写偏好。
否则部署方日后改默认值就再也传不过来——用户偏好里被冻住了一个他从未选过的档位。

只有用户真的动了控件（含选了「不使用」）才写，写的是他的表态本身。

#### 成年确认

`requires_adult` 为 true 的档位：

- Web 端 / 控制台：选中时弹出一次行内确认（「我已满 18 岁，开启」），确认后才落地；
- 桌宠窗：下拉项带 `（18+）` 后缀做**声明**——那是个常驻小窗，塞不下确认流程，
  所以确认动作只在能做好的两处做。

后端不拦截（该字段只做声明），所以这道确认是真实职责，不是装饰；
`frontend/tests/jailbreak-switcher.test.tsx` 守着它不能退化成一次点击就生效。

### 6.3 单轮级

```jsonc
POST /chat {"text": "...", "jailbreak_id": "strict-in-character"}  // 本轮开这一档
POST /chat {"text": "...", "jailbreak_id": "none"}                 // 本轮强制关
POST /chat {"text": "..."}                                         // 跟随部署默认（出厂=关）
```

三种语义**不能混**，混掉的后果是「用户关不掉」或「用户开不了」，且都是静默的：

| 传值 | 语义 |
| --- | --- |
| 未传 / `""` | 用部署默认（`JAILBREAK_ENABLED` 为 false 时即关闭） |
| `"none"` | 本轮强制关闭，覆盖部署默认 |
| 具体 id | 本轮显式指定档位，**同时隐含启用** |

为什么允许单轮主动开：开关是部署级的，但「这一轮想认真跑一段剧情」是会话级的临时意图。
强制先改 `.env` 再回来聊，会把一次即兴需求变成一次运维。

### 6.4 响应

`ChatResponse.jailbreak` 为**空 dict 即未启用**：

```jsonc
{"jailbreak_id": "immersive-narrative", "jailbreak_name": "沉浸叙事",
 "intensity": 1, "requires_adult": false}
```

判断时请用这个字段，**不要用 `settings.jailbreak_enabled`**——那只是部署默认，
用户可以在单轮里覆盖。

---

## 七、怎么加一个新档

1. 在 `backend/app/prompts/jailbreak/presets/` 新建 `<id>.yaml`（`id` 必须与文件名一致）；
2. 三段结构齐全，**尤其不要省 `<boundary>`**；
3. 重启后端（本层预设目前是进程内缓存的只读资源；接入工坊后可按需放开）。

字段定义见 `app/prompts/jailbreak/models.py`。

```yaml
id: my-preset
name: 我的框架
description: 一句话说明这层框架改变了什么
tags: [沉浸]
intensity: 1          # 0 轻度 / 1 中度 / 2 强度
requires_adult: false
conflicts_with: []    # 与人设冲突的关键词（只告警，不阻断）
jailbreak_prompt: |
  <fiction_frame>…</fiction_frame>
  <directive>…</directive>
  <boundary>…</boundary>
```

---

## 八、与合规的关系

本项目借鉴 SillyTavern 的提示词工程**思想**与数据格式规范，本层预设正文为原创实现。

需要明确的边界：

- 本层**只提供机制与槽位**，出厂不启用；是否启用、用哪一档由用户决定；
- 每个内置档自带 `<boundary>` 段，覆盖现实问题、伤害性操作、危机信号三条；
- 成人向档要求成年确认，并显式写明未成年人无例外；
- 修改或新增预设时请保留 `<boundary>`——测试会拦住漏掉它的档位。

相关文档：`docs/license-compliance.md`（许可证与第三方组件）。
