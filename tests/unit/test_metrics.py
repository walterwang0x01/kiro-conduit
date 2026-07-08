"""单元测试：runtime metrics 分桶 & reviewer 执行/审查拆分。"""

from __future__ import annotations

from kiro_conduit.metrics import (
    RuntimeMetricRecord,
    recommend_strategy,
    summarize_metrics,
)


def _rec(
    *,
    task_id: str,
    bucket: str,
    runtime_kind: str,
    model: str,
    passed: bool,
    execution_ok: bool | None = None,
    verdict_pass: bool | None = None,
    attempts: int = 1,
    files_changed: int = 0,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        task_id=task_id,
        runtime_kind=runtime_kind,
        model=model,
        passed=passed,
        attempts=attempts,
        files_changed=files_changed,
        task_bucket=bucket,
        execution_ok=execution_ok,
        verdict_pass=verdict_pass,
    )


class TestReviewerMetricsSeparation:
    def test_fail_verdict_still_counts_as_runtime_success(self) -> None:
        records = [
            _rec(
                task_id=f"r{i}",
                bucket="reviewer",
                runtime_kind="kiro-cli-acp",
                model="claude-sonnet-5",
                passed=True,
                execution_ok=True,
                # 找到了问题：verdict FAIL，但 runtime 跑通了
                verdict_pass=False,
            )
            for i in range(3)
        ]
        rows = summarize_metrics(records, bucket="reviewer")
        assert len(rows) == 1
        assert rows[0]["success"] == 3
        assert rows[0]["failed"] == 0
        assert rows[0]["success_rate"] == 1.0
        assert rows[0]["verdict_pass_rate"] == 0.0

    def test_crashed_reviewer_counts_as_runtime_failure(self) -> None:
        records = [
            _rec(
                task_id=f"r{i}",
                bucket="reviewer",
                runtime_kind="kiro-cli-acp",
                model="claude-opus-4.8",
                passed=False,
                execution_ok=False,
                verdict_pass=True,  # fail-open 给了 PASS，但 execution 失败
            )
            for i in range(3)
        ]
        rows = summarize_metrics(records, bucket="reviewer")
        assert rows[0]["success"] == 0
        assert rows[0]["failed"] == 3
        assert rows[0]["success_rate"] == 0.0

    def test_recommend_ignores_verdict_and_uses_execution(self) -> None:
        """审查 FAIL 多 ≠ 不要这个 runtime；只要执行稳，仍可推荐。"""
        records = [
            _rec(
                task_id=f"a{i}",
                bucket="reviewer",
                runtime_kind="kiro-cli-acp",
                model="claude-sonnet-5",
                passed=True,
                execution_ok=True,
                verdict_pass=False,
            )
            for i in range(3)
        ]
        rec = recommend_strategy(records, bucket="reviewer")
        assert rec["preferred_runtime_kind"] == "kiro-cli-acp"
        assert rec["preferred_model"] == "claude-sonnet-5"
        assert rec["runtime_success_rate"] == 1.0


class TestDurationAwareScoring:
    def test_faster_runtime_scores_higher_with_duration(self) -> None:
        slow = [
            _rec(
                task_id=f"s{i}",
                bucket="implementor",
                runtime_kind="kiro-cli-acp",
                model="claude-opus-4.8",
                passed=True,
                attempts=1,
                files_changed=1,
            )
            for i in range(3)
        ]
        # monkeypatch duration via replace
        from dataclasses import replace

        slow = [replace(r, duration_ms=120_000) for r in slow]
        fast = [
            replace(
                _rec(
                    task_id=f"f{i}",
                    bucket="implementor",
                    runtime_kind="cursor-agent-cli",
                    model="Auto",
                    passed=True,
                    attempts=1,
                    files_changed=1,
                ),
                duration_ms=5_000,
            )
            for i in range(3)
        ]
        rows = summarize_metrics([*slow, *fast], bucket="implementor")
        assert rows[0]["runtime_kind"] == "cursor-agent-cli"
        assert float(rows[0]["score"]) > float(rows[1]["score"])
        assert rows[0]["avg_duration_ms"] == 5000
