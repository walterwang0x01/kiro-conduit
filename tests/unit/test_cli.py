"""单元测试：CLI（kiro_conduit.cli）。

不调真 Kiro：monkeypatch ParallelOrchestrator.run / MergeOrchestrator.merge。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from kiro_conduit.cli import _resolve_dag, main
from kiro_conduit.merge import MergeOrchestrator, MergeReport, TaskMergeResult
from kiro_conduit.orchestrator import ParallelOrchestrator, ParallelRunReport
from kiro_conduit.roles.coordinator import CoordinatorOutcome
from kiro_conduit.types import LayerResult, TaskResult, VerifyLayer, VerifyResult


def _write_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "dag.yaml").write_text(
        dedent(
            """
            phases:
              - name: A
                type: serial
                tasks: [t1]
            tasks:
              t1: {spec: specs/t1.md}
            shared_files: []
            """
        ).lstrip(),
        encoding="utf-8",
    )
    specs = ws / "specs"
    specs.mkdir()
    (specs / "t1.md").write_text("t1\n")
    # git 仓库化：让 ws 自身可作 base_repo（CLI 预检要求是 git 仓库）
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, check=True, capture_output=True)
    return ws


def _passing(tid: str) -> CoordinatorOutcome:
    tr = TaskResult(task_id=tid, success=True, diff="", files_changed=[])
    vr = VerifyResult(
        task_id=tid,
        passed=True,
        layers=[LayerResult(layer=VerifyLayer.STATIC, passed=True, output="ok")],
        feedback="ok",
    )
    return CoordinatorOutcome(
        task_id=tid, passed=True, attempts=1,
        last_task_result=tr, last_verify_result=vr, history=[(tr, vr)],
    )


class TestPlanCommand:
    def test_plan_generates_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kiro_conduit import planner as planner_mod
        from kiro_conduit.dag import load_workspace, topological_waves
        from kiro_conduit.planner import TaskPlan

        spec = tmp_path / "spec.md"
        spec.write_text("build a small util lib", encoding="utf-8")
        out = tmp_path / "ws"

        async def fake_generate(self, spec_text, cwd):  # type: ignore[no-untyped-def]
            assert "small util" in spec_text
            return [
                TaskPlan(id="a", prompt="build a", files_owned=["a.py"]),
                TaskPlan(id="b", prompt="build b", depends_on=["a"], files_owned=["b.py"]),
            ]

        monkeypatch.setattr(planner_mod.KiroPlanner, "generate_plan", fake_generate)
        code = main(["plan", "--spec", str(spec), "--out", str(out)])
        assert code == 0
        # 生成了可加载的 dag.yaml + specs
        ws = load_workspace(out / "dag.yaml")
        assert set(ws.tasks) == {"a", "b"}
        assert topological_waves(ws) == [["a"], ["b"]]
        assert (out / "specs" / "a.md").read_text().startswith("build a")

    def test_plan_missing_spec_errors(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="spec file not found"):
            main(["plan", "--spec", str(tmp_path / "nope.md"), "--out", str(tmp_path / "ws")])


class TestResolveDag:
    def test_dir_with_dag(self, tmp_path: Path) -> None:
        ws = _write_ws(tmp_path)
        assert _resolve_dag(str(ws)) == (ws / "dag.yaml").resolve()

    def test_direct_file(self, tmp_path: Path) -> None:
        ws = _write_ws(tmp_path)
        dag = ws / "dag.yaml"
        assert _resolve_dag(str(dag)) == dag.resolve()

    def test_dir_without_dag(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="no dag"):
            _resolve_dag(str(tmp_path))

    def test_missing(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="not found"):
            _resolve_dag(str(tmp_path / "nope"))


class TestMain:
    def test_requires_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_preflight_rejects_non_git_base_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _write_ws(tmp_path)
        non_git = tmp_path / "nongit"
        non_git.mkdir()

        async def fake_run(self, base_branch: str = "main") -> ParallelRunReport:  # type: ignore[no-untyped-def]
            raise AssertionError("should not reach run(): preflight must fail first")

        monkeypatch.setattr(ParallelOrchestrator, "run", fake_run)
        with pytest.raises(SystemExit, match="not a git repository"):
            main(["run", "--workspace", str(ws), "--base-repo", str(non_git)])

    def test_run_no_merge_exit0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _write_ws(tmp_path)

        async def fake_run(self, base_branch: str = "main") -> ParallelRunReport:  # type: ignore[no-untyped-def]
            return ParallelRunReport(
                outcomes={"t1": _passing("t1")}, skipped=(), handles={}
            )

        monkeypatch.setattr(ParallelOrchestrator, "run", fake_run)
        # 默认即 review 模式（不合并）
        code = main(["run", "--workspace", str(ws)])
        assert code == 0

    def test_run_full_invokes_merge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _write_ws(tmp_path)
        merged: dict[str, bool] = {}

        async def fake_run(self, base_branch: str = "main") -> ParallelRunReport:  # type: ignore[no-untyped-def]
            return ParallelRunReport(
                outcomes={"t1": _passing("t1")}, skipped=(), handles={}
            )

        async def fake_merge(  # type: ignore[no-untyped-def]
            self, handles, successful_task_ids, base_branch="main", commit_messages=None
        ):
            merged["called"] = True
            return MergeReport(
                results={"t1": TaskMergeResult(task_id="t1", merged=True)},
                stopped_at=None,
            )

        monkeypatch.setattr(ParallelOrchestrator, "run", fake_run)
        monkeypatch.setattr(MergeOrchestrator, "merge", fake_merge)
        code = main(["run", "--workspace", str(ws), "--merge"])
        assert code == 0
        assert merged.get("called") is True

    def test_base_branch_defaults_to_current_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _write_ws(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
        (repo / "f").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "i"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "checkout", "-b", "feature/x"], cwd=repo, check=True, capture_output=True
        )

        captured: dict[str, str] = {}

        async def fake_run(self, base_branch: str = "main") -> ParallelRunReport:  # type: ignore[no-untyped-def]
            captured["bb"] = base_branch
            return ParallelRunReport(
                outcomes={"t1": _passing("t1")}, skipped=(), handles={}
            )

        monkeypatch.setattr(ParallelOrchestrator, "run", fake_run)
        code = main(["run", "--workspace", str(ws), "--base-repo", str(repo)])
        assert code == 0
        assert captured["bb"] == "feature/x"  # 跟随当前分支，不是 main

    def test_run_failed_tasks_skip_merge_exit1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _write_ws(tmp_path)

        async def fake_run(self, base_branch: str = "main") -> ParallelRunReport:  # type: ignore[no-untyped-def]
            # 有 skipped → all_passed False
            return ParallelRunReport(
                outcomes={"t1": _passing("t1")}, skipped=("t2",), handles={}
            )

        called = {"merge": False}

        async def fake_merge(self, *a, **k):  # type: ignore[no-untyped-def]
            called["merge"] = True
            return MergeReport(results={}, stopped_at=None)

        monkeypatch.setattr(ParallelOrchestrator, "run", fake_run)
        monkeypatch.setattr(MergeOrchestrator, "merge", fake_merge)
        # 即使显式 --merge，任务失败也应短路，不进 merge
        code = main(["run", "--workspace", str(ws), "--merge"])
        assert code == 1
        assert called["merge"] is False  # 失败时不该进 merge
