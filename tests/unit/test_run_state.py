"""单元测试：run-state 持久化。"""

from __future__ import annotations

from pathlib import Path

from lwa_conduit.run_state import (
    RunState,
    TaskRunStatus,
    load_state,
    save_state,
    state_path,
)


class TestRunState:
    def test_record_and_passed_ids(self) -> None:
        rs = RunState(base_branch="main")
        rs.record("t1", TaskRunStatus.PASSED, branch="lwa-conduit/t1", attempts=1)
        rs.record("t2", TaskRunStatus.FAILED, branch="lwa-conduit/t2", attempts=3)
        rs.record("t3", TaskRunStatus.SKIPPED)
        assert rs.passed_ids() == {"t1"}
        assert rs.tasks["t2"].attempts == 3

    def test_roundtrip_save_load(self, tmp_path: Path) -> None:
        rs = RunState(base_branch="develop")
        rs.record("t1", TaskRunStatus.PASSED, branch="lwa-conduit/t1", attempts=2)
        p = state_path(tmp_path)
        save_state(p, rs)
        assert p.is_file()

        loaded = load_state(p)
        assert loaded is not None
        assert loaded.base_branch == "develop"
        assert loaded.tasks["t1"].status is TaskRunStatus.PASSED
        assert loaded.tasks["t1"].branch == "lwa-conduit/t1"
        assert loaded.tasks["t1"].attempts == 2

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_state(state_path(tmp_path)) is None

    def test_load_corrupt_returns_none(self, tmp_path: Path) -> None:
        p = state_path(tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not valid json", encoding="utf-8")
        assert load_state(p) is None

    def test_load_wrong_version_returns_none(self, tmp_path: Path) -> None:
        p = state_path(tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"version": 999, "base_branch": "main", "tasks": {}}', encoding="utf-8")
        assert load_state(p) is None

    def test_save_is_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        p = state_path(tmp_path)
        save_state(p, RunState(base_branch="main"))
        # 写完不留 .tmp
        assert not p.with_name(p.name + ".tmp").exists()

    def test_record_overwrites(self, tmp_path: Path) -> None:
        rs = RunState(base_branch="main")
        rs.record("t1", TaskRunStatus.FAILED, attempts=1)
        rs.record("t1", TaskRunStatus.PASSED, branch="b", attempts=2)
        assert rs.tasks["t1"].status is TaskRunStatus.PASSED
        assert rs.passed_ids() == {"t1"}


class TestRunStateContextRecovery:
    """run_state 的上下文恢复扩展（failure feedback + failed_summary）。"""

    def test_record_failure_with_feedback(self) -> None:
        rs = RunState(base_branch="main")
        rs.record(
            "t1",
            TaskRunStatus.FAILED,
            branch="lwa-conduit/t1",
            attempts=2,
            last_failure_feedback="[dynamic failed] test_auth failed: AssertionError",
            last_failed_layer="dynamic",
        )
        assert rs.tasks["t1"].last_failure_feedback is not None
        assert "test_auth" in rs.tasks["t1"].last_failure_feedback
        assert rs.tasks["t1"].last_failed_layer == "dynamic"

    def test_failed_summary(self) -> None:
        rs = RunState(base_branch="main")
        rs.record("t1", TaskRunStatus.PASSED, attempts=1)
        rs.record(
            "t2",
            TaskRunStatus.FAILED,
            attempts=3,
            last_failure_feedback="lint error: missing type hint",
            last_failed_layer="static",
        )
        rs.record("t3", TaskRunStatus.SKIPPED)
        summary = rs.failed_summary()
        assert "t1" not in summary
        assert "t3" not in summary
        assert "t2" in summary
        feedback, layer = summary["t2"]
        assert feedback == "lint error: missing type hint"
        assert layer == "static"

    def test_roundtrip_preserves_feedback(self, tmp_path: Path) -> None:
        rs = RunState(base_branch="main")
        rs.record(
            "t1",
            TaskRunStatus.FAILED,
            branch="lwa-conduit/t1",
            attempts=2,
            last_failure_feedback="contract violation: signature changed",
            last_failed_layer="contract",
        )
        p = state_path(tmp_path)
        save_state(p, rs)

        loaded = load_state(p)
        assert loaded is not None
        assert loaded.tasks["t1"].last_failure_feedback == "contract violation: signature changed"
        assert loaded.tasks["t1"].last_failed_layer == "contract"

    def test_old_format_without_feedback_still_loads(self, tmp_path: Path) -> None:
        """兼容性：旧版 run-state 没有 feedback 字段仍然能加载。"""
        import json

        p = state_path(tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        old_format = {
            "version": 1,
            "base_branch": "main",
            "tasks": {
                "t1": {"status": "passed", "branch": "b1", "attempts": 1}
            },
        }
        p.write_text(json.dumps(old_format), encoding="utf-8")
        loaded = load_state(p)
        assert loaded is not None
        assert loaded.tasks["t1"].status is TaskRunStatus.PASSED
        assert loaded.tasks["t1"].last_failure_feedback is None
        assert loaded.tasks["t1"].last_failed_layer is None
