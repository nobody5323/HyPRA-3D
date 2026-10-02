# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格（--onedir）。

产物：`dist/backend/backend.exe` + `_internal/`

资源布局必须与 `app/paths.py` 的 `resource_root()` 约定一致
（frozen 形态下 resource_root() = sys._MEIPASS）：

    _MEIPASS/app/prompts/...      ← 人设/文风/叙事框架(jailbreak)/适配 yaml
                                  （由下面的 `app/**/*.yaml` 通配覆盖，
                                    新增 prompts 子目录无需改本文件）
    _MEIPASS/app/llm/profiles.yaml
    _MEIPASS/app/worldbook/entries/
    _MEIPASS/plugins/             ← 第一方插件
    _MEIPASS/skills/              ← 内置技能
    _MEIPASS/mcp_servers.json     ← MCP 清单

可写数据（data/ media/ 日志等）**不打包**：frozen 形态下由
`paths.data_root()` 指向 `%APPDATA%/HyPRA`。

**可选依赖**（装了才收，不装也能构建）：
语音识别（`faster-whisper`）需要额外声明两处，否则冻结形态下直接不可用
——原因与实测结论见 `docs/proactive-multimodal.md` §4.3。
"""

import glob
import os

# ---- 只读资源 ----------------------------------------------------------
# 保持相对目录结构，落到 _MEIPASS 下同名位置
datas = [(f, os.path.dirname(f)) for f in glob.glob("app/**/*.yaml", recursive=True)]
# 创作提示词（人设 / 技能 / 插件）是 .md，不在上面的 yaml 通配里：
# 漏了它们在开发形态下照常工作，打包后却会 FileNotFoundError。
datas += [
    ("app/prompts/authoring", "app/prompts/authoring"),
]
datas += [
    ("plugins", "plugins"),
    ("skills", "skills"),
    # MCP server 脚本：打包形态下由 backend.exe --mcp-server 自举执行
    ("mcp_servers", "mcp_servers"),
    ("mcp_servers.json", "."),
]

# ---- 隐式导入 ----------------------------------------------------------
# uvicorn 的协议/事件循环实现是运行时按名字动态 import 的（形如
# f"uvicorn.protocols.http.{name}"），静态分析看不到，必须显式声明。
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
]

# ---- 额外二进制 --------------------------------------------------------
binaries: list = []

# ---- 可选依赖：本地语音识别（faster-whisper）--------------------------
# 它**不在主依赖里**（见 pyproject.toml 的 `[asr]`）：拖三个原生扩展、约 90MB。
# 因此「装没装」都可能——装了就把原生库与 VAD 资源收进来，没装就跳过。
# spec 不能因为一个可选依赖缺席而报错（那会让「不装 ASR」的构建直接失败）。
try:
    from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

    import faster_whisper  # noqa: F401  仅为探测是否可导入

    # `assets/silero_vad_v6.onnx`（1.2MB）：VAD 的模型，faster-whisper 按
    # **包内相对路径**加载。不收它的话，冻结形态下 `vad_filter=True` 会直接
    # FileNotFoundError——而 VAD 默认开（Whisper 的幻听问题，见
    # docs/proactive-multimodal.md §4.3），等于语音输入整个不可用。
    datas += collect_data_files("faster_whisper")

    # `ctranslate2.dll`（约 58MB）是 ctranslate2 包目录下的**普通 DLL**，
    # 不是扩展模块——PyInstaller 只自动收扩展模块（.pyd）与 Python 包，
    # 普通 DLL 要显式收集。缺了它 `import ctranslate2` 直接失败。
    binaries += collect_dynamic_libs("ctranslate2")

    hiddenimports += ["faster_whisper", "ctranslate2", "tokenizers"]
except ImportError:
    # 未安装 `[asr]`：冻结产物里 ASR 不可用（`GET /perception/status` 会如实
    # 回 `asr_available=false`，界面据此不渲染麦克风）。这是**受支持的形态**，
    # 不是降级——语音输入本来就是可选能力。
    pass

a = Analysis(
    ["run_backend.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 排除开发期依赖与非必需的重型可选依赖（减小体积）
    excludes=["pytest", "_pytest", "tkinter", "matplotlib", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="backend",
)
