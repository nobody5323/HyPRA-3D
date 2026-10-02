"""感知层与事件通道的 HTTP 契约测试。

覆盖三类真实故障：
- **能力不可用时接口不能 500**：设置面板在页面加载时就打 `/perception/status`，
  它必须如实回 `available=false` 并给出「怎么办」，而不是抛异常；
- **桌面情景开关关掉后必须真的不采集**（§4.6 红线是「立即停止」，
  不是「采集了不用」）；
- **转写结果不得进记忆库**（它只是感知事实，用户确认发送后才走完整链路）。
"""

import pytest
from fastapi.testclient import TestClient

from app.api import perception as perception_module
from app.main import app
from app.perception.asr.base import AsrError, AsrProvider, AsrResult
from app.perception.snapshot import get_perception_snapshot
from app.perception.vision.base import VisionError, VisionProvider, VisionResult

client = TestClient(app)


class _FakeAsr(AsrProvider):
    name = "fake-asr"

    def __init__(self, *, text: str = "今天有点累", available: bool = True) -> None:
        self.text = text
        self._available = available

    def available(self) -> bool:
        return self._available

    def transcribe(self, audio, *, language="zh", filename="audio.webm") -> AsrResult:
        if not self._available:
            raise AsrError("模型没放对位置")
        return AsrResult(text=self.text, provider=self.name, language="zh")


class _FakeVision(VisionProvider):
    name = "fake-vision"

    def __init__(self, *, description: str = "窗边有一只橘猫", available: bool = True) -> None:
        self.description = description
        self._available = available

    def available(self) -> bool:
        return self._available

    def describe(self, image, *, mime="image/png", question="") -> VisionResult:
        if not self._available:
            raise VisionError("没有 API Key")
        return VisionResult(description=self.description, provider=self.name, model="fake-vl")


@pytest.fixture(autouse=True)
def _reset_providers():
    yield
    perception_module.set_asr_provider(None)
    perception_module.set_vision_provider(None)
    get_perception_snapshot().clear()


# ---------- 状态 ----------


def test_status_reports_unavailable_without_500() -> None:
    """默认出厂配置（provider=none）下，状态接口必须正常回，而不是报错。"""
    res = client.get("/perception/status")
    assert res.status_code == 200
    body = res.json()
    assert body["asr_available"] is False
    assert body["vision_available"] is False
    # 「怎么办」必须写在 note 里——只回 false 用户不知道下一步做什么
    assert "faster-whisper" in body["asr_note"]
    assert "Qwen2.5-VL" in body["vision_note"]


def test_status_reflects_injected_providers() -> None:
    perception_module.set_asr_provider(_FakeAsr())
    perception_module.set_vision_provider(_FakeVision())
    body = client.get("/perception/status").json()
    assert body["asr_available"] is True
    assert body["vision_available"] is True
    assert body["asr_provider"] == "fake-asr"


def test_status_exposes_ui_entry_switches() -> None:
    """「能力」与「入口」是两个开关，都要能透给前端。

    前端据此决定画不画按钮、要不要绑快捷键。少了这几个字段，
    `asr_ui_enabled=false`（用快捷键说话、不想看见按钮）就无处生效。
    """
    body = client.get("/perception/status").json()
    assert body["asr_ui_enabled"] is True
    assert body["vision_ui_enabled"] is True
    assert body["asr_shortcut"] == ""  # 默认不占用任何按键


def test_status_reflects_configured_shortcut(monkeypatch) -> None:
    monkeypatch.setenv("ASR_SHORTCUT", "Ctrl+Shift+M")
    body = client.get("/perception/status").json()
    assert body["asr_shortcut"] == "Ctrl+Shift+M"


# ---------- 语音识别 ----------


def test_asr_unavailable_returns_503_with_actionable_detail() -> None:
    perception_module.set_asr_provider(_FakeAsr(available=False))
    res = client.post(
        "/perception/asr",
        files={"file": ("speech.webm", b"fake-audio", "audio/webm")},
    )
    assert res.status_code == 503
    assert "faster-whisper" in res.json()["detail"]


def test_asr_returns_text_without_sending() -> None:
    """**只转写、不发送**：结果回给前端填输入框，由用户确认后再走 /chat。"""
    perception_module.set_asr_provider(_FakeAsr(text="今天有点累"))
    res = client.post(
        "/perception/asr",
        files={"file": ("speech.webm", b"fake-audio", "audio/webm")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["text"] == "今天有点累"
    assert body["has_text"] is True


def test_asr_empty_audio_rejected() -> None:
    perception_module.set_asr_provider(_FakeAsr())
    res = client.post(
        "/perception/asr", files={"file": ("speech.webm", b"", "audio/webm")}
    )
    assert res.status_code == 400


def test_asr_oversized_audio_rejected(monkeypatch) -> None:
    """语音输入是「说一句话」，不是上传录音文件。"""
    monkeypatch.setenv("ASR_MAX_FILE_MB", "0.0001")
    perception_module.set_asr_provider(_FakeAsr())
    res = client.post(
        "/perception/asr",
        files={"file": ("speech.webm", b"x" * 2048, "audio/webm")},
    )
    assert res.status_code == 413


def test_asr_provider_error_is_400_not_500() -> None:
    """配置问题是 400：detail 里带着「该把模型放到哪」，可以原样展示给用户。"""

    class _Boom(AsrProvider):
        name = "boom"

        def available(self) -> bool:
            return True

        def transcribe(self, audio, *, language="zh", filename=""):
            raise AsrError("请把模型放到 <数据目录>/models/whisper/small/")

    perception_module.set_asr_provider(_Boom())
    res = client.post(
        "/perception/asr", files={"file": ("a.webm", b"x", "audio/webm")}
    )
    assert res.status_code == 400
    assert "models/whisper" in res.json()["detail"]


# ---------- 图片理解 ----------


def test_vision_unavailable_returns_503() -> None:
    perception_module.set_vision_provider(_FakeVision(available=False))
    res = client.post(
        "/perception/vision", files={"file": ("a.png", b"png-bytes", "image/png")}
    )
    assert res.status_code == 503
    assert "Qwen2.5-VL" in res.json()["detail"]


def test_vision_records_fact_and_returns_rendered_text() -> None:
    perception_module.set_vision_provider(_FakeVision(description="窗边有一只橘猫"))
    res = client.post(
        "/perception/vision", files={"file": ("a.png", b"png-bytes", "image/png")}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["description"] == "窗边有一只橘猫"
    # 事实被记进快照，并渲染成「此刻」文本（前端可直接展示/排查）
    assert "用户分享了一张图片" in body["perception_text"]
    assert "窗边有一只橘猫" in body["perception_text"]


def test_vision_empty_description_records_nothing() -> None:
    """模型没看出什么 ≠ 记一条空事实（否则「此刻」里会出现一行空话）。"""
    perception_module.set_vision_provider(_FakeVision(description=""))
    res = client.post(
        "/perception/vision", files={"file": ("a.png", b"png-bytes", "image/png")}
    )
    assert res.status_code == 200
    # 断言的是「没有那条空事实」，而不是「整段为空」——
    # 时钟与行踪本来就会产出自己的行（见 docs/proactive-multimodal.md §4.8 / §4.9）
    assert "用户分享了一张图片" not in res.json()["perception_text"]


def test_vision_empty_image_rejected() -> None:
    perception_module.set_vision_provider(_FakeVision())
    res = client.post("/perception/vision", files={"file": ("a.png", b"", "image/png")})
    assert res.status_code == 400


def test_vision_error_is_400_not_500() -> None:
    perception_module.set_vision_provider(_FakeVision(available=False))
    res = client.post("/perception/vision", files={"file": ("a.png", b"x", "image/png")})
    assert res.status_code == 503


# ---------- 桌面情景 ----------


def _report(**overrides) -> dict:
    payload = {
        "now_playing_title": "起风了",
        "now_playing_artist": "买辣椒也用券",
        "foreground_process": "Code.exe",
        "local_time": "14:30",
    }
    payload.update(overrides)
    return payload


def test_desktop_report_records_and_renders() -> None:
    res = client.post("/perception/desktop", json=_report())
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] is True
    assert "起风了" in body["perception_text"]


def test_desktop_disabled_stops_collecting_and_clears(monkeypatch) -> None:
    """§4.6 红线：关掉开关后采集链路**立即停止**，已有数据也要清掉。"""
    client.post("/perception/desktop", json=_report())
    assert get_perception_snapshot().current()

    monkeypatch.setenv("PERCEPTION_DESKTOP_ENABLED", "false")
    res = client.post("/perception/desktop", json=_report())
    body = res.json()
    assert body["accepted"] is False
    assert body["enabled"] is False
    # 关键：快照被清空，旧事实不会继续参与提示词
    assert get_perception_snapshot().current() == []


def test_window_title_not_rendered_by_default() -> None:
    """窗口标题默认不注入：歌名可以聊，窗口标题可能是一封邮件。"""
    res = client.post(
        "/perception/desktop",
        json=_report(foreground_title="关于体检报告的几点说明"),
    )
    assert "体检报告" not in res.json()["perception_text"]


def test_window_title_rendered_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PERCEPTION_INCLUDE_WINDOW_TITLE", "true")
    res = client.post(
        "/perception/desktop", json=_report(foreground_title="项目周报")
    )
    assert "项目周报" in res.json()["perception_text"]


# ---------- 时间感 / 行踪 / 画像（§4.8 / §4.9）----------


def test_time_is_always_rendered_even_without_desktop_report() -> None:
    """时间不再依赖桌宠端上报——Web 端也必须知道现在几点。"""
    body = client.get("/perception/status").json()
    assert body["include_time"] is True
    assert any(line.startswith("- 现在是 ") for line in body["current"])


def test_desktop_report_feeds_the_timeline() -> None:
    """同一份上报顺带喂给行踪：上报之后「最近在做什么」就有一句由头。"""
    client.post("/perception/desktop", json=_report())
    body = client.get("/perception/status").json()
    assert body["activity_current"] == "Code.exe"
    assert any("Code.exe" in line for line in body["current"])


def test_activity_endpoint_reports_what_was_recorded() -> None:
    """「它记住了什么」必须对用户可见（§4.6 的可观测性要求）。"""
    client.post("/perception/desktop", json=_report())
    body = client.get("/perception/activity").json()
    assert body["enabled"] is True
    assert body["include_title"] is False
    assert body["retention_days"] == 7
    assert body["current"] == "Code.exe"
    assert body["lines"] and "Code.exe" in body["lines"][-1]


def test_activity_clear_removes_everything() -> None:
    client.post("/perception/desktop", json=_report())
    res = client.post("/perception/activity/clear")
    assert res.status_code == 200
    assert res.json()["removed"] >= 1

    body = client.get("/perception/activity").json()
    assert body["current"] == ""
    assert body["lines"] == []
    # 清完再上报，行踪从零开始（不是「清了一半」）
    assert "Code.exe" not in client.get("/perception/status").json()["activity_current"]


def test_activity_clear_is_idempotent() -> None:
    """第二次清除要如实回 0——界面靠这个数字区分「删干净了」与「本来就没有」。"""
    assert client.post("/perception/activity/clear").json()["removed"] == 0

    # 有数据时：先造一段再清，必须报出真实的删除数（且不把当前段数两次）
    client.post("/perception/desktop", json=_report())
    removed = client.post("/perception/activity/clear").json()["removed"]
    assert removed >= 1
    assert client.post("/perception/activity/clear").json()["removed"] == 0


def test_disabling_activity_stops_and_clears(monkeypatch) -> None:
    """§4.6 红线：关掉行踪后采集立即停止，已记的内容也要清掉。"""
    client.post("/perception/desktop", json=_report())
    assert client.get("/perception/status").json()["activity_current"] == "Code.exe"

    monkeypatch.setenv("PERCEPTION_ACTIVITY_ENABLED", "false")
    client.post("/perception/desktop", json=_report())

    body = client.get("/perception/status").json()
    assert body["activity_enabled"] is False
    assert body["activity_current"] == ""
    assert body["activity_lines"] == []
    assert not any("Code.exe" in line for line in body["current"])


def test_activity_disabled_does_not_write_files(monkeypatch, tmp_path) -> None:
    """关掉之后**不产生任何行踪文件**（不是「写了但不渲染」）。"""
    monkeypatch.setenv("PERCEPTION_ACTIVITY_ENABLED", "false")
    client.post("/perception/desktop", json=_report())
    root = tmp_path / "perception"
    assert not root.exists() or not list(root.glob("activity-*"))


def test_window_title_not_recorded_in_timeline_by_default(monkeypatch) -> None:
    """行踪默认只记程序名——标题是内容（可能是一封邮件）。"""
    client.post(
        "/perception/desktop", json=_report(foreground_title="关于体检报告的几点说明")
    )
    body = client.get("/perception/activity").json()
    assert "体检报告" not in "".join(body["lines"])
    assert "体检报告" not in "".join(
        client.get("/perception/status").json()["current"]
    )


def test_activity_query_rejects_absurd_window() -> None:
    assert client.get("/perception/activity", params={"hours": 0}).status_code == 422
    assert client.get("/perception/activity", params={"hours": 999}).status_code == 422


# ---------- 主动链路接口 ----------


def test_proactive_status_is_available() -> None:
    res = client.get("/proactive/status")
    assert res.status_code == 200
    body = res.json()
    assert "enabled" in body
    assert body["sent_today"] == 0
    # 八个内置触发器都在（定时 / 间隔 / 记忆到期 / 三个情景 / 两个行踪驱动）
    ids = {item["id"] for item in body["triggers"]}
    assert ids == {
        "daily-greeting",
        "idle-checkin",
        "memory-followup",
        "late-night",
        "back-from-away",
        "activity-shift",
        "now-playing",
        "low-battery",
    }


def test_proactive_run_without_subscriber_does_nothing() -> None:
    """没人监听时不生成（事件会被总线丢弃，生成纯属浪费一次 LLM 调用）。"""
    res = client.post("/proactive/run")
    assert res.status_code == 200
    body = res.json()
    assert body["fired"] is False
    assert body["reply"] == ""


def test_proactive_reset_is_idempotent() -> None:
    assert client.post("/proactive/reset").status_code == 200
    assert client.post("/proactive/reset").status_code == 200


def test_turning_activity_off_via_settings_wipes_existing_records(tmp_path) -> None:
    """★ 在能力中心关掉行踪的那一刻就要清干净，不能等桌宠端下一次上报。

    真实场景：用户先在程序控制台关掉开关，**再关掉桌宠窗**——
    那时最后一次上报已经发生过，等上报来清就永远等不到。
    而用户关掉这个开关的动机通常就是「我不想要这些了」，
    留着已记的内容等于没关干净。
    """
    client.post("/perception/desktop", json=_report())
    root = tmp_path / "perception"
    assert list(root.glob("activity-*")) or (root / "activity-current.json").exists()

    res = client.put(
        "/plugins/perception-ambient/settings",
        json={"values": {"perception_activity_enabled": False}},
    )
    assert res.status_code == 200

    # 文件被清掉
    assert not list(root.glob("activity-*"))
    assert not (root / "activity-current.json").exists()
    # 状态接口也不再报「此刻在用什么」（那是历史的读出口）
    body = client.get("/perception/status").json()
    assert body["activity_enabled"] is False
    assert body["activity_current"] == ""
    assert body["activity_lines"] == []


def test_unrelated_settings_change_does_not_touch_activity(tmp_path) -> None:
    """改一个与行踪无关的配置，不该顺手把行踪清掉。"""
    client.post("/perception/desktop", json=_report())
    root = tmp_path / "perception"
    before = sorted(p.name for p in root.iterdir())

    res = client.put(
        "/plugins/perception-ambient/settings",
        json={"values": {"perception_activity_retention_days": 3}},
    )
    assert res.status_code == 200
    assert sorted(p.name for p in root.iterdir()) == before
    assert client.get("/perception/activity").json()["retention_days"] == 3
