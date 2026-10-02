"""打包规格（`backend.spec`）的静态守卫。

为什么值得单独测：这里两处遗漏的**失败模式是「开发能跑、装进安装包就坏」**——
而且都不是报错，是「语音输入整个不可用」或「import 直接失败」。
单元测试与开发形态的冒烟**都测不到**它们，只有真的打包并跑起来才暴露
（实测结论见 `docs/proactive-multimodal.md` §4.3）。

所以这里用**源码级断言**把结论钉住：不需要真的跑 PyInstaller（那要一分钟以上，
不适合放进单测），只校验 spec 里该声明的声明了、该保护的保护了。
与前端 `desktop/tests/pet-renderer-boundary.test.ts` 同一套思路。
"""

import ast
import importlib.util
from pathlib import Path

import pytest

SPEC_PATH = Path(__file__).resolve().parents[2] / "backend.spec"


@pytest.fixture(scope="module")
def spec_source() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def test_spec_is_valid_python(spec_source: str) -> None:
    """spec 本身是 Python 文件，语法错误会让打包在第一步就失败。"""
    ast.parse(spec_source)


def test_spec_declares_optional_asr_collection(spec_source: str) -> None:
    """装了 `[asr]` 时，必须显式收集两样 PyInstaller 收不到的东西。

    - `faster_whisper/assets/*.onnx`（VAD 模型）：按**包内相对路径**加载，
      漏了它 `vad_filter=True` 直接 FileNotFoundError，而 VAD 默认开
      → 语音输入整个不可用；
    - `ctranslate2.dll`（约 57MB）：包目录下的**普通 DLL**，不是扩展模块，
      PyInstaller 只自动收 `.pyd` 与 Python 包 → 漏了它 `import ctranslate2` 失败。
    """
    if importlib.util.find_spec("faster_whisper") is None:
        pytest.skip("未安装可选依赖 [asr]：本机不涉及这条收集规则")

    assert 'collect_data_files("faster_whisper")' in spec_source, (
        "spec 缺少 faster_whisper 的资源收集——VAD 模型会缺失，"
        "冻结形态下语音输入整个不可用"
    )
    assert 'collect_dynamic_libs("ctranslate2")' in spec_source, (
        "spec 缺少 ctranslate2 的动态库收集——ctranslate2.dll 是普通 DLL，"
        "不会被自动收集，冻结形态下 import ctranslate2 直接失败"
    )


def test_optional_collection_is_guarded(spec_source: str) -> None:
    """可选依赖的收集必须包在 try/except 里。

    `[asr]` 是**可选**依赖：不装它也必须能构建成功（那是一种受支持的形态，
    产物里 `asr_available=false`，界面不渲染麦克风）。
    没保护的话，spec 会在 `import faster_whisper` 处直接崩，
    「不装 ASR 的构建」整个失败。
    """
    assert "except ImportError" in spec_source, (
        "可选依赖的收集没有 try/except 保护——不装 [asr] 时构建会直接失败"
    )
    # 保护必须真的罩住那两句收集调用（顺序：try → collect_* → except）
    try_at = spec_source.index("try:")
    collect_at = spec_source.index('collect_data_files("faster_whisper")')
    except_at = spec_source.index("except ImportError")
    assert try_at < collect_at < except_at, "收集调用不在 try/except 的保护范围内"


def test_binaries_are_passed_to_analysis(spec_source: str) -> None:
    """`binaries` 必须作为变量传给 Analysis。

    曾经它是写死的 `binaries=[]`：那样即便上面收集到了动态库，
    也传不进 Analysis，等于白收集——而这类「改了变量但没接上」的疏漏
    不会报错，只是安静地不生效。
    """
    tree = ast.parse(spec_source)
    analysis_call = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ),
        None,
    )
    assert analysis_call is not None, "spec 里找不到 Analysis(...) 调用"

    binaries_kwarg = next(
        (kw for kw in analysis_call.keywords if kw.arg == "binaries"), None
    )
    assert binaries_kwarg is not None, "Analysis(...) 没有 binaries 参数"
    assert isinstance(binaries_kwarg.value, ast.Name), (
        "binaries 应是变量（`binaries=binaries`）而不是字面量 "
        "`[]`——写死会让收集到的动态库传不进去"
    )
