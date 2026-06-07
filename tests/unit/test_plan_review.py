"""单元测试：交互式 plan review。"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO

from rich.console import Console

from kiro_conduit.plan_review import review_plan
from kiro_conduit.planner import DimensionResult, PlanEvaluation, TaskPlan


def _make_tasks() -> list[TaskPlan]:
    return [
        TaskPlan(id="t1", prompt="build module A", files_owned=["src/a.py"]),
        TaskPlan(id="t2", prompt="build module B", depends_on=["t1"], files_owned=["src/b.py"]),
        TaskPlan(id="t3", prompt="build module C", files_owned=["src/c.py"]),
    ]


def _make_confirm_fn(responses: list[str]) -> tuple[Iterator[str], list[str]]:
    """创建一个假的 confirm_fn，按顺序返回预设回答。"""
    it = iter(responses)
    calls: list[str] = []

    def fn(prompt: str) -> str:
        calls.append(prompt)
        return next(it, "a")

    return fn, calls  # type: ignore[return-value]


class TestReviewPlan:
    def test_accept_all(self) -> None:
        """输入 A → 全部接受。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn(["a"])  # Accept all
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert len(result) == 3
        assert [t.id for t in result] == ["t1", "t2", "t3"]

    def test_accept_all_empty_input(self) -> None:
        """直接回车（空字符串）→ 全部接受。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn([""])
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert len(result) == 3

    def test_review_one_by_one_accept_all(self) -> None:
        """选择 review，然后逐个 accept。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn(["r", "a", "a", "a"])
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert len(result) == 3

    def test_skip_one(self) -> None:
        """跳过第二个 task。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn(["r", "a", "s", "a"])
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert len(result) == 2
        assert [t.id for t in result] == ["t1", "t3"]

    def test_edit_prompt(self) -> None:
        """编辑第一个 task 的 prompt。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn(["r", "e", "new prompt for A", "a", "a"])
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert len(result) == 3
        assert result[0].prompt == "new prompt for A"
        assert result[0].id == "t1"  # id 不变

    def test_edit_prompt_empty_keeps_original(self) -> None:
        """编辑时直接回车 → 保持原 prompt。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn(["r", "e", "", "a", "a"])
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert result[0].prompt == "build module A"

    def test_with_evaluation_display(self) -> None:
        """有自评结果时正常运行不崩。"""
        tasks = _make_tasks()
        dim = DimensionResult(score=80, issues=[])
        evaluation = PlanEvaluation(
            score=75,
            coverage=dim,
            granularity=dim,
            coupling=dim,
            dependencies=dim,
            clarity=dim,
            spec_alignment=dim,
            must_fix=["需要加 depends_on"],
            suggestions=["prompt 可以更具体"],
        )
        fn, _ = _make_confirm_fn(["a"])
        console = Console(file=StringIO())
        result = review_plan(tasks, evaluation=evaluation, console=console, confirm_fn=fn)
        assert len(result) == 3

    def test_skip_all(self) -> None:
        """全部跳过 → 返回空列表。"""
        tasks = _make_tasks()
        fn, _ = _make_confirm_fn(["r", "s", "s", "s"])
        console = Console(file=StringIO())
        result = review_plan(tasks, console=console, confirm_fn=fn)
        assert len(result) == 0
