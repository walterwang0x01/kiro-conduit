"""把 EventBus 事件导出为稳定 NDJSON，供 Bridge 等父进程解析。

协议：`lwa.conduit.event/v1`，一行一条 JSON，默认写 stderr（stdout 留给人读日志）。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, is_dataclass
from typing import Any, TextIO

from lwa_conduit.events import Event, EventBus

SCHEMA = "lwa.conduit.event/v1"


def event_to_dict(event: Event) -> dict[str, Any]:
    """dataclass Event → 可 JSON 序列化的 dict（含 schema / type / ts）。"""
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "type": type(event).__name__,
        "ts": time.time(),
    }
    if is_dataclass(event) and not isinstance(event, type):
        for key, value in asdict(event).items():
            if isinstance(value, tuple):
                payload[key] = list(value)
            else:
                payload[key] = value
    return payload


def event_to_ndjson_line(event: Event) -> str:
    return json.dumps(event_to_dict(event), ensure_ascii=False, separators=(",", ":"))


class NdjsonEventWriter:
    """订阅 EventBus，把事件刷到文本流（默认 stderr）。"""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._unsubscribe: Any = None

    def attach(self, bus: EventBus) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
        self._unsubscribe = bus.subscribe(self._on_event)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _on_event(self, event: Event) -> None:
        line = event_to_ndjson_line(event)
        print(line, file=self._stream, flush=True)
