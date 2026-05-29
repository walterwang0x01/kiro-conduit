"""单元测试：EventBus 发布订阅。"""

from __future__ import annotations

from kiro_conduit.events import (
    EventBus,
    LockEvent,
    MergeFinished,
    MergeStarted,
    RunCompleted,
    TaskFinished,
    TaskStarted,
    WaveStarted,
)


class TestEventBus:
    def test_subscribe_and_publish(self) -> None:
        bus = EventBus()
        received: list = []
        bus.subscribe(lambda e: received.append(e))
        evt = WaveStarted(wave_index=1, total_waves=2, task_ids=("a",))
        bus.publish(evt)
        assert received == [evt]

    def test_multiple_subscribers(self) -> None:
        bus = EventBus()
        a, b = [], []
        bus.subscribe(a.append)
        bus.subscribe(b.append)
        evt = TaskStarted(task_id="t1", attempt=1, max_attempts=3)
        bus.publish(evt)
        assert a == [evt]
        assert b == [evt]

    def test_no_subscribers(self) -> None:
        bus = EventBus()
        # 不该崩
        bus.publish(WaveStarted(wave_index=1, total_waves=1, task_ids=()))

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        received: list = []
        unsub = bus.subscribe(received.append)
        bus.publish(TaskFinished(task_id="x", attempt=1, passed=True))
        assert len(received) == 1
        unsub()
        bus.publish(TaskFinished(task_id="x", attempt=2, passed=False))
        assert len(received) == 1  # 未变化

    def test_subscriber_exception_swallowed(self) -> None:
        """一个订阅者抛异常不应影响其他订阅者。"""
        bus = EventBus()
        good_received: list = []

        def bad_callback(_e: object) -> None:
            raise RuntimeError("boom")

        bus.subscribe(bad_callback)
        bus.subscribe(good_received.append)

        evt = TaskFinished(task_id="x", attempt=1, passed=True)
        bus.publish(evt)  # 不该抛
        assert good_received == [evt]

    def test_subscriber_count(self) -> None:
        bus = EventBus()
        assert bus.subscriber_count() == 0
        unsub = bus.subscribe(lambda _: None)
        assert bus.subscriber_count() == 1
        unsub()
        assert bus.subscriber_count() == 0

    def test_unsubscribe_twice_safe(self) -> None:
        bus = EventBus()
        unsub = bus.subscribe(lambda _: None)
        unsub()
        unsub()  # 不该崩


class TestEventTypes:
    """各事件类型构造正常。"""

    def test_wave_started(self) -> None:
        e = WaveStarted(wave_index=1, total_waves=3, task_ids=("a", "b"))
        assert e.task_ids == ("a", "b")
        assert e.skipped_ids == ()

    def test_task_started_finished(self) -> None:
        s = TaskStarted(task_id="t1", attempt=1, max_attempts=3)
        assert s.attempt == 1
        f = TaskFinished(task_id="t1", attempt=2, passed=False, failed_layer="static")
        assert f.failed_layer == "static"

    def test_lock_event(self) -> None:
        e = LockEvent(
            file_path="src/x.py",
            task_id="t1",
            action="acquired",
            policy="single-writer",
        )
        assert e.action == "acquired"

    def test_merge_events(self) -> None:
        s = MergeStarted(task_id="t1")
        assert s.task_id == "t1"
        f = MergeFinished(task_id="t1", merged=False, error="conflict")
        assert not f.merged

    def test_run_completed(self) -> None:
        rc = RunCompleted(passed_count=3, failed_count=1, skipped_count=0)
        assert rc.passed_count == 3
