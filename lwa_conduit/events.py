"""进程内事件总线：让 orchestrator 等组件 publish 事件，dashboard 订阅渲染。

设计要点：
- 纯同步 publish：发布者 fire-and-forget，不等订阅者
- 订阅者注册一个 callable，被同步调用——慢回调会拖慢 publish，由订阅者自己保证快
  （dashboard 的回调只更新内存状态，rich Live 自己起异步线程渲染）
- 事件用 dataclass 定义，结构化好认（避免 dict[str, Any] 的灾难）
- EventBus 没全局单例：调用方自己持有实例传给需要 publish 的组件，None 表示不发
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaveStarted:
    """orchestrator 进入新一波 wave。"""

    wave_index: int       # 1-based
    total_waves: int
    task_ids: tuple[str, ...]
    skipped_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskStarted:
    """单个 task 的 worker 起来了。"""

    task_id: str
    attempt: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class TaskFinished:
    """单个 task 的 worker 跑完一次（含失败）。"""

    task_id: str
    attempt: int
    passed: bool
    failed_layer: str | None = None  # static / dynamic / semantic / contract


@dataclass(frozen=True, slots=True)
class LockEvent:
    """锁的 acquire / release。"""

    file_path: str
    task_id: str
    action: str  # 'acquired' / 'released' / 'rejected'
    policy: str  # 'single-writer' / 'append-only' / 'coordinator-only'


@dataclass(frozen=True, slots=True)
class MergeStarted:
    """串行 merge 开始处理某个 task。"""

    task_id: str


@dataclass(frozen=True, slots=True)
class MergeFinished:
    """单个 task 的 merge 结果。"""

    task_id: str
    merged: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunCompleted:
    """整个 orchestrator.run 退出（成功或失败）。"""

    passed_count: int
    failed_count: int
    skipped_count: int


# 所有事件的联合类型——dashboard 等订阅者用 isinstance 分流
Event = (
    WaveStarted
    | TaskStarted
    | TaskFinished
    | LockEvent
    | MergeStarted
    | MergeFinished
    | RunCompleted
)


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


Subscriber = Callable[[Event], None]


class EventBus:
    """进程内同步发布订阅。"""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """注册回调。返回一个取消函数（call 它 = 取消订阅）。"""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    def publish(self, event: Event) -> None:
        """同步发给所有订阅者。任意订阅者抛异常被吞下并 log（不能让 publisher 受影响）。"""
        for cb in list(self._subscribers):  # 拷贝防止迭代时修改
            try:
                cb(event)
            except Exception:
                logger.exception(
                    "[event-bus] subscriber raised handling %s; ignored",
                    type(event).__name__,
                )

    def subscriber_count(self) -> int:
        return len(self._subscribers)
