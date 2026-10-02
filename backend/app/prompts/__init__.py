"""prompts 包：分层 System Prompt 模板与渲染。

- persona/      人设预设（YAML，与代码分离）
- state_vars/   动态状态变量定义与宏替换
- style/        表达文风预设（与人设正交）
- jailbreak/    叙事框架层（术语对齐 ST 的 jailbreak 槽位；**默认关闭**）
- renderer.py   人设渲染 + token 估算（唯一 token 入口）
- assemble.py   世界书命中块的注入编排（priority + budget 守卫）
- sanitize.py   回复清洗（去除模型泄漏的元信息）

三层预设的分工（正交，可任意组合）：

    persona/      谁在说话
    style/        怎么说话
    jailbreak/    这段对话处在什么框架里（虚构叙事 / 现实问答）

`jailbreak/` 只声明框架，不描述性格、不描述语气；每档必带 `<boundary>` 段
（框架之外仍然成立的红线），由测试强制存在。设计见 `docs/jailbreak-layer.md`。

注意：本包只管「渲染与拼块」。分层组装、总量预算与优先级裁剪见
app/rag/prompt_manager.py；一次对话的节点编排见 app/graph/nodes.py。
"""
