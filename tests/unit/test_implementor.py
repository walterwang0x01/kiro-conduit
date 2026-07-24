"""单元测试：Implementor 退避重试（瞬时 ACP 错误）。

不调真 Kiro：monkeypatch _run_acp 控制成功/失败，patch asyncio.sleep 免真等待。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import lwa_conduit.roles.implementor as impl_mod
from lwa_conduit.roles.implementor import Implementor
from lwa_conduit.types import Task


def _task(tmp_path: Path) -> Task:
    return Task(id="t1", prompt="do it", cwd=tmp_path, acceptance=[])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """patch asyncio.sleep：不真等，记录每次退避时长。"""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(impl_mod.asyncio, "sleep", fake_sleep)
    return slept


@pytest.fixture(autouse=True)
def _stub_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功路径会收集 git 改动，stub 成有 1 个文件。"""
    async def fake_list(cwd: Path) -> list[str]:
        return ["f.py"]

    async def fake_diff(cwd: Path) -> str:
        return "diff"

    monkeypatch.setattr(impl_mod, "list_changed_files", fake_list)
    monkeypatch.setattr(impl_mod, "collect_diff", fake_diff)


@pytest.mark.asyncio
async def test_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """前两次连接失败，第三次成功 → 整体成功，退避了两次（1s, 2s）。"""
    calls = {"n": 0}

    async def flaky(self, task: Task) -> list[str]:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("boom")
        return ["ok"]

    monkeypatch.setattr(Implementor, "_run_acp", flaky)
    impl = Implementor(max_retries=2, retry_base_delay=1.0)
    result = await impl.run(_task(tmp_path))

    assert result.success
    assert calls["n"] == 3
    assert _no_sleep == [1.0, 2.0]  # 指数退避


@pytest.mark.asyncio
async def test_exhausts_retries_then_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """一直超时 → max_retries 用尽后返回失败，共尝试 max_retries+1 次。"""
    calls = {"n": 0}

    async def always_timeout(self, task: Task) -> list[str]:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise TimeoutError("nope")

    monkeypatch.setattr(Implementor, "_run_acp", always_timeout)
    impl = Implementor(max_retries=2, retry_base_delay=0.5)
    result = await impl.run(_task(tmp_path))

    assert not result.success
    assert "TimeoutError" in (result.error or "")
    assert calls["n"] == 3  # 1 + 2 retries
    assert _no_sleep == [0.5, 1.0]


@pytest.mark.asyncio
async def test_no_retry_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """一次就成功不退避。"""
    calls = {"n": 0}

    async def ok(self, task: Task) -> list[str]:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return ["ok"]

    monkeypatch.setattr(Implementor, "_run_acp", ok)
    result = await Implementor(max_retries=2).run(_task(tmp_path))

    assert result.success
    assert calls["n"] == 1
    assert _no_sleep == []


@pytest.mark.asyncio
async def test_retries_on_acp_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """ACP -32603 内部错误视为瞬时：退避重试，第三次成功。"""
    from lwa_conduit.acp import AcpError

    calls = {"n": 0}

    async def flaky(self, task: Task) -> list[str]:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] < 3:
            raise AcpError(code=-32603, message="Internal error")
        return ["ok"]

    monkeypatch.setattr(Implementor, "_run_acp", flaky)
    result = await Implementor(max_retries=2).run(_task(tmp_path))
    assert result.success
    assert calls["n"] == 3  # 重试了两次


@pytest.mark.asyncio
async def test_acp_deterministic_error_fails_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """ACP -32601(方法不存在)是确定性错误：不重试，但优雅判失败而非崩。"""
    from lwa_conduit.acp import AcpError

    calls = {"n": 0}

    async def always(self, task: Task) -> list[str]:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise AcpError(code=-32601, message="Method not found")

    monkeypatch.setattr(Implementor, "_run_acp", always)
    result = await Implementor(max_retries=2).run(_task(tmp_path))
    assert not result.success  # 优雅失败
    assert "AcpError" in (result.error or "")
    assert calls["n"] == 1  # 没重试（确定性错误）


@pytest.mark.asyncio
async def test_start_log_includes_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """启动日志记录该任务用的模型，便于事后审计。"""
    async def ok(self, task: Task) -> list[str]:  # type: ignore[no-untyped-def]
        return ["ok"]

    monkeypatch.setattr(Implementor, "_run_acp", ok)
    with caplog.at_level("INFO", logger="lwa_conduit.roles.implementor"):
        await Implementor(model="claude-haiku-4.5").run(_task(tmp_path))
    assert "model=claude-haiku-4.5" in caplog.text

    caplog.clear()
    with caplog.at_level("INFO", logger="lwa_conduit.roles.implementor"):
        await Implementor().run(_task(tmp_path))  # 没指定 → <default>
    assert "model=<default>" in caplog.text


class _FakeAcpClient:
    """假的 AcpClient，满足 _run_kiro_acp 用到的接口子集，驱动指定事件流。"""

    def __init__(self, events: list[object]) -> None:
        self._events = events

    @classmethod
    async def spawn(cls, config: object = None) -> _FakeAcpClient:
        raise NotImplementedError  # 被 monkeypatch 替换，不会真的走到这里

    async def __aenter__(self) -> _FakeAcpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def initialize(self) -> dict[str, object]:
        return {}

    async def new_session(self, cwd: Path) -> str:
        return "sess-1"

    async def prompt(self, session_id: str, text: str):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            for ev in self._events:
                yield ev

        return gen()


@pytest.mark.asyncio
async def test_run_kiro_acp_logs_tool_call_events_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    用户真实反馈：implementor 调用 kiro-cli 生成代码期间完全没有任何输出，
    看起来像卡住（真实观察：一次 /conduit run 里出现 3 次超过 45 秒的
    完全静默窗口，正对应这一步）。ToolCallEvent 是天然的进度里程碑
    （"正在写文件 X"），到达时应该立即打日志，不能只静默 append 进
    transcript。
    """
    import lwa_conduit.roles.implementor as impl_mod
    from lwa_conduit.acp import ToolCallEvent, TurnEnd

    events = [
        ToolCallEvent(
            session_id="sess-1", tool_call_id="1", name="fs_write", status="running", raw={}
        ),
        ToolCallEvent(
            session_id="sess-1", tool_call_id="1", name="fs_write", status="completed", raw={}
        ),
        TurnEnd(session_id="sess-1", stop_reason="end_turn"),
    ]

    async def fake_spawn(config: object = None) -> _FakeAcpClient:
        return _FakeAcpClient(events)

    monkeypatch.setattr(impl_mod.AcpClient, "spawn", fake_spawn)
    impl = Implementor()
    with caplog.at_level("INFO", logger="lwa_conduit.roles.implementor"):
        transcript, _ = await impl._run_kiro_acp(_task(tmp_path))

    assert "tool running: fs_write" in caplog.text
    assert "tool completed: fs_write" in caplog.text
    assert any("[tool running] fs_write" in part for part in transcript)


@pytest.mark.asyncio
async def test_run_kiro_acp_logs_heartbeat_for_long_message_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    AgentMessageChunk 逐字符打日志会刷屏，但完全不打又会看起来像卡住。
    按累计字符数节流：攒够 200 字符打一条"generating…"心跳。
    """
    import lwa_conduit.roles.implementor as impl_mod
    from lwa_conduit.acp import AgentMessageChunk, TurnEnd

    # 5 个 chunk，每个 50 字符，累计 250 字符 → 应该触发 1 次心跳（满 200 那次）
    events: list[object] = [AgentMessageChunk(session_id="sess-1", text="x" * 50) for _ in range(5)]
    events.append(TurnEnd(session_id="sess-1", stop_reason="end_turn"))

    async def fake_spawn(config: object = None) -> _FakeAcpClient:
        return _FakeAcpClient(events)

    monkeypatch.setattr(impl_mod.AcpClient, "spawn", fake_spawn)
    impl = Implementor()
    with caplog.at_level("INFO", logger="lwa_conduit.roles.implementor"):
        transcript, _ = await impl._run_kiro_acp(_task(tmp_path))

    assert "generating…" in caplog.text
    assert "".join(transcript) == "x" * 250


@pytest.mark.asyncio
async def test_run_kiro_acp_no_heartbeat_when_chunks_stay_under_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """总字符数没到 200 阈值：不应该有任何 generating… 心跳（避免噪音）。"""
    import lwa_conduit.roles.implementor as impl_mod
    from lwa_conduit.acp import AgentMessageChunk, TurnEnd

    events: list[object] = [AgentMessageChunk(session_id="sess-1", text="short")]
    events.append(TurnEnd(session_id="sess-1", stop_reason="end_turn"))

    async def fake_spawn(config: object = None) -> _FakeAcpClient:
        return _FakeAcpClient(events)

    monkeypatch.setattr(impl_mod.AcpClient, "spawn", fake_spawn)
    impl = Implementor()
    with caplog.at_level("INFO", logger="lwa_conduit.roles.implementor"):
        await impl._run_kiro_acp(_task(tmp_path))

    assert "generating…" not in caplog.text
