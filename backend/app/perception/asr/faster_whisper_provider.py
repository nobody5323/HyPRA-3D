"""`faster-whisper`：本地语音识别（本次采用的实现）。

## 三条实现纪律

1. **惰性导入 + 惰性加载**。`faster_whisper` 拖进 `ctranslate2` / `onnxruntime`
   两个原生扩展，导入本身就慢；模型加载更慢。因此模块级不 import，
   模型在首次 `transcribe` / `warmup` 时才加载，之后缓存在实例上。
2. **模型权重不进发布包**（`AGENTS.md §3.2` 的同一条原则）。
   模型放 `data_root()/models/whisper/`（打包形态 = `%APPDATA%/HyPRA/models/whisper/`），
   由用户自备；找不到时**报清楚位置**，而不是抛一个 HF 的网络栈。
3. **不静默下载**。`faster-whisper` 在只给模型名时会去 HuggingFace 拉权重——
   国内网络下这既慢又常失败，且用户不知道自己机器上正在下 1GB 东西。
   所以：本地目录有模型就用本地；没有就**明确报错并给出放置路径**，
   让「要不要下载」由用户决定。

## 「幻听」：为什么必须开 VAD，还要再过滤一次

Whisper 在**没有人声**的音频上不会老实返回空串，而是会吐出训练集里的水印文本。
实测（`small` 模型 + 一段 440Hz 纯音）：识别结果是 **「字幕by索兰娅」**。

这在语音输入场景里是真问题：麦克风拾到环境噪音/键盘声/纯音乐时，
这句水印会被当成用户说的话填进输入框——用户会以为「它自己乱打字」。

两道防线：

| 防线 | 作用 |
| --- | --- |
| `vad_filter=True`（Silero VAD 预切非语音段） | 从源头上让模型「听不到」没人声的部分 |
| `_looks_like_hallucination()` 文本过滤 | 兜住 VAD 漏掉的（有声音但非人声：音乐、键盘声） |

过滤**只认锚定在开头的固定套路**（`^字幕` / `^请订阅` / `^thanks for watching` …）
并加了长度上限。不用「包含关键词」判据——那会误杀「我订阅了那个频道」这种正常发言，
而误杀用户的话比漏掉一句水印严重得多。
"""

from __future__ import annotations

import io
import logging
import re
import threading
from pathlib import Path

from app.perception.asr.base import AsrError, AsrProvider, AsrResult

logger = logging.getLogger(__name__)

#: 一个可用的 faster-whisper 模型目录至少要有这个文件
_MODEL_MARKER = "model.bin"

#: 支持的模型规格（仅用于报错提示里列举可选值）
KNOWN_SIZES = ("tiny", "base", "small", "medium", "large-v3")

#: 疑似幻听的**开头**套路。锚定 `^` 是刻意的：用「包含」判据会误杀
#: 「我订阅了那个频道」这类正常发言。
_HALLUCINATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^字幕",              # 「字幕by索兰娅」「字幕志愿者」
        r"^请不吝",
        r"^请订阅",
        r"^明镜与点点",
        r"^(谢谢|感谢)(观看|收看)",
        r"^thanks?\s+for\s+watching",
        r"^subtitles?\s+by",
        r"^www\.",
    )
)

#: 幻听文本的长度上限：这类水印都是一行短语。超过它的即使命中套路也不丢——
#: 那更像用户真的说了什么，而误杀用户的话比漏掉一句水印严重得多。
_HALLUCINATION_MAX_CHARS = 50


def _looks_like_hallucination(text: str) -> bool:
    """是否像 Whisper 的训练集水印（而不是用户真的说了什么）。"""
    stripped = text.strip()
    if not stripped or len(stripped) > _HALLUCINATION_MAX_CHARS:
        return False
    return any(pattern.search(stripped) for pattern in _HALLUCINATION_PATTERNS)


class FasterWhisperAsrProvider(AsrProvider):
    """本地 faster-whisper 转写。"""

    name = "faster-whisper"

    def __init__(
        self,
        *,
        model: str = "small",
        model_dir: str = "",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        download_root: str = "",
        vad_filter: bool = True,
        no_speech_threshold: float = 0.6,
    ) -> None:
        self.model_name = (model or "small").strip()
        self.model_dir = (model_dir or "").strip()
        self.device = (device or "cpu").strip()
        self.compute_type = (compute_type or "int8").strip()
        self.beam_size = int(beam_size)
        #: 允许 faster-whisper 自己下载时的落点（仅当用户显式给了非空值才传给它）
        self.download_root = (download_root or "").strip()
        #: 用 Silero VAD 先切掉非语音段。**默认开**，理由见模块文档「幻听」一节
        self.vad_filter = bool(vad_filter)
        self.no_speech_threshold = float(no_speech_threshold)

        self._model = None
        self._load_error: str | None = None
        # 模型加载不是线程安全的（两个请求同时首次进入会各加载一份，显存/内存翻倍）
        self._load_lock = threading.Lock()

    # ---------- 依赖与模型解析 ----------

    @staticmethod
    def _import_backend():
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise AsrError(
                "未安装 faster-whisper。请在后端环境执行：\n"
                "    pip install faster-whisper\n"
                "（它会一并安装 ctranslate2 与 onnxruntime）"
            ) from exc
        return WhisperModel

    def _resolve_model_ref(self) -> str:
        """决定用「本地模型目录」还是「模型名」。

        顺序（先本地、后名字）：
        1. `<model_dir>/<model>/model.bin` 存在 → 用该子目录
           （按规格分目录存放，便于同时留着 tiny 和 small）；
        2. `<model_dir>/model.bin` 存在 → 直接用 `model_dir`
           （用户只放了一份模型、没建规格子目录）；
        3. 否则 → 用模型名，交给 faster-whisper 自己解析
           （它可能命中本地 HF 缓存，也可能去下载——下载失败时的报错在
           `transcribe` 里被换成一句带放置路径的中文提示）。
        """
        if self.model_dir:
            root = Path(self.model_dir)
            candidate = root / self.model_name
            if (candidate / _MODEL_MARKER).is_file():
                return str(candidate)
            if (root / _MODEL_MARKER).is_file():
                return str(root)
        return self.model_name

    def _load(self):
        """加载模型（线程安全、只做一次）。失败时缓存错误原因，不反复重试。"""
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            raise AsrError(self._load_error)

        with self._load_lock:
            if self._model is not None:
                return self._model
            if self._load_error is not None:
                raise AsrError(self._load_error)

            WhisperModel = self._import_backend()
            model_ref = self._resolve_model_ref()
            kwargs: dict = {
                "device": self.device,
                "compute_type": self.compute_type,
            }
            if self.download_root:
                kwargs["download_root"] = self.download_root

            try:
                logger.info(
                    "加载 ASR 模型：%s（device=%s, compute_type=%s）",
                    model_ref,
                    self.device,
                    self.compute_type,
                )
                self._model = WhisperModel(model_ref, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 换成能照做的中文提示
                self._load_error = self._explain_load_failure(exc, model_ref)
                logger.warning("ASR 模型加载失败：%s", self._load_error)
                raise AsrError(self._load_error) from exc
            return self._model

    def _explain_load_failure(self, exc: Exception, model_ref: str) -> str:
        """把底层异常翻译成「用户能照做」的一句话。

        底层报错通常是 HF 的连接栈或一句 `Unable to open file 'model.bin'`，
        两者都不告诉用户「该把模型放哪」——而那才是唯一能解决问题的信息。
        """
        target = self.model_dir or "<数据目录>/models/whisper"
        return (
            f"语音识别模型「{self.model_name}」加载失败：{exc}\n"
            f"请把 faster-whisper 模型权重放到：{target}/{self.model_name}/\n"
            f"（该目录下应有 model.bin、config.json、tokenizer.json 等文件；"
            f"可用规格：{' / '.join(KNOWN_SIZES)}）\n"
            f"当前解析到的模型引用：{model_ref}"
        )

    # ---------- 对外接口 ----------

    def available(self) -> bool:
        """依赖是否已安装（**不**加载模型——那太慢，不该在状态查询里做）。"""
        try:
            self._import_backend()
        except AsrError:
            return False
        return True

    def warmup(self) -> None:
        """预热：把模型加载进内存，让第一次说话不必等十几秒。

        失败**不抛出**：预热是优化，不是功能。真正的失败会在首次转写时
        以完整的中文提示暴露出来（这里只记日志，避免启动日志里出现吓人的栈）。
        """
        try:
            self._load()
            logger.info("ASR 预热完成：%s", self.model_name)
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响后端可用
            logger.warning("ASR 预热失败（首次使用时才会再试）：%s", exc)

    def transcribe(
        self, audio: bytes, *, language: str = "zh", filename: str = "audio.webm"
    ) -> AsrResult:
        model = self._load()
        if not audio:
            raise AsrError("音频为空。")

        try:
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language=(language or None),
                beam_size=self.beam_size,
                # 关掉「上一条结果作为下一条的提示」：短语音输入下它会
                # 把上一句的词带进下一句，在对话场景里表现为「莫名其妙重复了一句」
                condition_on_previous_text=False,
                # VAD 预过滤 + 无语音阈值：**这对参数不是调优，是正确性**
                # （见模块文档「幻听」一节）
                vad_filter=self.vad_filter,
                no_speech_threshold=self.no_speech_threshold,
            )
            text = "".join(segment.text for segment in segments).strip()
        except AsrError:
            raise
        except Exception as exc:  # noqa: BLE001 - 解码失败要能给出可读原因
            raise AsrError(
                f"音频转写失败：{exc}（常见原因：浏览器录音格式不被支持，"
                "或音频为空。可尝试改用 webm/opus 之外的格式）"
            ) from exc

        # 转写成功但内容像是训练集水印时丢弃：VAD 已经挡掉大部分，
        # 但模型仍可能对「有声音但不是人声」的片段（键盘声、音乐）产出这类文本
        if text and _looks_like_hallucination(text):
            logger.info("丢弃疑似幻听文本：%r", text[:40])
            text = ""

        return AsrResult(
            text=text,
            provider=self.name,
            language=getattr(info, "language", "") or "",
            duration_seconds=float(getattr(info, "duration", 0.0) or 0.0),
            warnings=[] if text else ["没有识别到语音内容。"],
        )
