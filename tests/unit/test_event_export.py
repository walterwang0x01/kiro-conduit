"""event_export：EventBus → NDJSON 协议。"""

from __future__ import annotations

import io
import json

from lwa_conduit.event_export import (
    SCHEMA,
    NdjsonEventWriter,
    event_to_dict,
    event_to_ndjson_line,
)
from lwa_conduit.events import (
    EventBus,
    LockEvent,
    MergeFinished,
    MergeStarted,
    RunCompleted,
    TaskFinished,
    TaskStarted,
    WaveStarted,
)


def test_event_to_dict_wave_started() -> None:
    ev = WaveStarted(wave_index=1, total_waves=3, task_ids=("a", "b"), skipped_ids=("c",))
    d = event_to_dict(ev)
    assert d["schema"] == SCHEMA
    assert d["type"] == "WaveStarted"
    assert isinstance(d["ts"], float)
    assert d["wave_index"] == 1
    assert d["total_waves"] == 3
    assert d["task_ids"] == ["a", "b"]
    assert d["skipped_ids"] == ["c"]


def test_event_to_dict_all_types_json_serializable() -> None:
    samples = [
        WaveStarted(1, 1, ("t1",)),
        TaskStarted("t1", 1, 3),
        TaskFinished("t1", 1, True, None),
        TaskFinished("t2", 2, False, "static"),
        LockEvent("src/a.py", "t1", "acquired", "single-writer"),
        MergeStarted("t1"),
        MergeFinished("t1", True, None),
        MergeFinished("t2", False, "conflict"),
        RunCompleted(1, 1, 0),
    ]
    for ev in samples:
        line = event_to_ndjson_line(ev)
        parsed = json.loads(line)
        assert parsed["schema"] == SCHEMA
        assert parsed["type"] == type(ev).__name__
        assert "\n" not in line


def test_ndjson_writer_subscribes_and_emits() -> None:
    buf = io.StringIO()
    bus = EventBus()
    writer = NdjsonEventWriter(stream=buf)
    writer.attach(bus)
    bus.publish(TaskStarted("pkg", 1, 2))
    bus.publish(RunCompleted(1, 0, 0))
    writer.detach()
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "TaskStarted"
    assert json.loads(lines[1])["type"] == "RunCompleted"
    # detach 后不再写
    bus.publish(TaskFinished("pkg", 1, True))
    assert len([ln for ln in buf.getvalue().splitlines() if ln.strip()]) == 2
