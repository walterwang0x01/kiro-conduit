"""单元测试：planner 纯核心（不调 LLM）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from kiro_conduit.dag import load_workspace, topological_waves
from kiro_conduit.planner import (
    PLAN_PROMPT,
    PlanError,
    TaskPlan,
    compute_layers,
    parse_plan,
    render_dag_yaml,
    write_plan,
)

_PLAN_JSON = """
some preamble...
```json
{
  "tasks": [
    {
      "id": "a", "prompt": "build a",
      "files_owned": ["a.py"], "acceptance": ["pytest -q test_a.py"]
    },
    {"id": "b", "prompt": "build b", "depends_on": ["a"], "files_owned": ["b.py"]}
  ]
}
```
trailing text
"""


class TestParsePlan:
    def test_parses_fenced_json_with_noise(self) -> None:
        tasks = parse_plan(_PLAN_JSON)
        assert [t.id for t in tasks] == ["a", "b"]
        assert tasks[0].files_owned == ["a.py"]
        assert tasks[1].depends_on == ["a"]

    def test_parses_bare_json(self) -> None:
        tasks = parse_plan('{"tasks":[{"id":"x","prompt":"do x"}]}')
        assert tasks[0].id == "x"

    def test_rejects_non_json(self) -> None:
        with pytest.raises(PlanError, match="parse plan JSON"):
            parse_plan("no json here")

    def test_rejects_empty_tasks(self) -> None:
        with pytest.raises(PlanError, match="non-empty 'tasks'"):
            parse_plan('{"tasks": []}')

    def test_rejects_missing_id(self) -> None:
        with pytest.raises(PlanError, match="missing non-empty 'id'"):
            parse_plan('{"tasks":[{"prompt":"x"}]}')

    def test_rejects_duplicate_id(self) -> None:
        with pytest.raises(PlanError, match="duplicate task id"):
            parse_plan('{"tasks":[{"id":"a","prompt":"x"},{"id":"a","prompt":"y"}]}')

    def test_rejects_missing_prompt(self) -> None:
        with pytest.raises(PlanError, match="missing non-empty 'prompt'"):
            parse_plan('{"tasks":[{"id":"a"}]}')


class TestComputeLayers:
    def test_linear_chain(self) -> None:
        tasks = [
            TaskPlan(id="a", prompt="a"),
            TaskPlan(id="b", prompt="b", depends_on=["a"]),
            TaskPlan(id="c", prompt="c", depends_on=["b"]),
        ]
        assert compute_layers(tasks) == [["a"], ["b"], ["c"]]

    def test_parallel_layer(self) -> None:
        tasks = [
            TaskPlan(id="root", prompt="r"),
            TaskPlan(id="x", prompt="x", depends_on=["root"]),
            TaskPlan(id="y", prompt="y", depends_on=["root"]),
        ]
        assert compute_layers(tasks) == [["root"], ["x", "y"]]

    def test_unknown_dependency(self) -> None:
        with pytest.raises(PlanError, match="unknown task"):
            compute_layers([TaskPlan(id="a", prompt="a", depends_on=["ghost"])])

    def test_cycle(self) -> None:
        tasks = [
            TaskPlan(id="a", prompt="a", depends_on=["b"]),
            TaskPlan(id="b", prompt="b", depends_on=["a"]),
        ]
        with pytest.raises(PlanError, match="cycle"):
            compute_layers(tasks)


class TestRenderAndWrite:
    def test_render_is_loadable(self, tmp_path: Path) -> None:
        tasks = parse_plan(_PLAN_JSON)
        dag = render_dag_yaml(tasks)
        p = tmp_path / "dag.yaml"
        p.write_text(dag, encoding="utf-8")
        # 必须能被 dag 加载器解析且校验通过
        (tmp_path / "specs").mkdir()
        (tmp_path / "specs" / "a.md").write_text("a")
        (tmp_path / "specs" / "b.md").write_text("b")
        ws = load_workspace(p)
        assert set(ws.tasks) == {"a", "b"}
        assert topological_waves(ws) == [["a"], ["b"]]

    def test_write_plan_creates_files_and_validates(self, tmp_path: Path) -> None:
        tasks = parse_plan(_PLAN_JSON)
        out = tmp_path / "ws"
        dag_path = write_plan(tasks, out)
        assert dag_path == out / "dag.yaml"
        assert (out / "specs" / "a.md").read_text().startswith("build a")
        assert (out / "specs" / "b.md").read_text().startswith("build b")
        # 已通过 write_plan 内部 load_workspace 校验；再确认波次
        ws = load_workspace(dag_path)
        assert topological_waves(ws) == [["a"], ["b"]]

    def test_write_plan_rejects_cycle(self, tmp_path: Path) -> None:
        tasks = [
            TaskPlan(id="a", prompt="a", depends_on=["b"]),
            TaskPlan(id="b", prompt="b", depends_on=["a"]),
        ]
        with pytest.raises(PlanError, match="cycle"):
            write_plan(tasks, tmp_path / "ws")

    def test_write_plan_overlap_raises_planerror_but_keeps_files(
        self, tmp_path: Path
    ) -> None:
        """files_owned 重叠 → 校验失败应转成 PlanError（不是裸 DagError），
        且 dag.yaml 仍落盘，供人按提示修正。"""
        tasks = [
            TaskPlan(id="a", prompt="a", files_owned=["src/x.py"]),
            TaskPlan(id="b", prompt="b", files_owned=["src/x.py"]),
        ]
        out = tmp_path / "ws"
        with pytest.raises(PlanError, match="校验"):
            write_plan(tasks, out)
        assert (out / "dag.yaml").is_file()  # 产物保留，供手动修正


class TestPlanPrompt:
    """PLAN_PROMPT 应指示把项目 linter 纳入每个 task 的 acceptance。"""

    def test_prompt_requires_linter_in_acceptance(self) -> None:
        assert "linter" in PLAN_PROMPT and "ruff check" in PLAN_PROMPT

    def test_prompt_biases_toward_pr_sized_tasks(self) -> None:
        # 粒度启发：PR 大小 + 别拆强耦合
        assert "PR" in PLAN_PROMPT
        assert "强耦合" in PLAN_PROMPT


class TestPlanValidationAndRepair:
    """plan_validation_error 纯校验 + KiroPlanner 自动修复重试。"""

    def test_validation_detects_overlap(self) -> None:
        from kiro_conduit.planner import plan_validation_error
        tasks = [
            TaskPlan(id="a", prompt="a", files_owned=["src/x.py"]),
            TaskPlan(id="b", prompt="b", files_owned=["src/x.py"]),
        ]
        err = plan_validation_error(tasks)
        assert err is not None and "src/x.py" in err

    def test_validation_detects_cycle(self) -> None:
        from kiro_conduit.planner import plan_validation_error
        tasks = [
            TaskPlan(id="a", prompt="a", depends_on=["b"]),
            TaskPlan(id="b", prompt="b", depends_on=["a"]),
        ]
        assert plan_validation_error(tasks) is not None

    def test_validation_passes_clean(self) -> None:
        from kiro_conduit.planner import plan_validation_error
        tasks = [
            TaskPlan(id="a", prompt="a", files_owned=["src/a.py"]),
            TaskPlan(id="b", prompt="b", files_owned=["src/b.py"], depends_on=["a"]),
        ]
        assert plan_validation_error(tasks) is None

    @pytest.mark.asyncio
    async def test_generate_plan_auto_repairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """首拆 files_owned 重叠 → 自动把错误喂回、第二次拆干净 → 返回修好的。"""
        from kiro_conduit.planner import KiroPlanner

        bad = '{"tasks":[{"id":"a","prompt":"a","files_owned":["src/x.py"]},' \
              '{"id":"b","prompt":"b","files_owned":["src/x.py"]}]}'
        good = '{"tasks":[{"id":"a","prompt":"a","files_owned":["src/a.py"]},' \
               '{"id":"b","prompt":"b","files_owned":["src/b.py"]}]}'
        calls: list[str] = []

        async def fake_ask(self, prompt: str, cwd: Path) -> str:  # type: ignore[no-untyped-def]
            calls.append(prompt)
            return bad if len(calls) == 1 else good

        monkeypatch.setattr(KiroPlanner, "_ask", fake_ask)
        tasks = await KiroPlanner().generate_plan("spec", tmp_path)
        assert {t.id for t in tasks} == {"a", "b"}
        assert len(calls) == 2  # 修复了一次
        assert "校验" in calls[1] or "没通过" in calls[1] or "src/x.py" in calls[1]
