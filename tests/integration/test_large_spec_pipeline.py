"""M2 集成测试：大规格 DAG（17 任务 / 8 波次）stub 端到端。

不调真 Kiro——验证波次调度、run-state、merge 在较大 DAG 上仍稳定。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kiro_conduit.dag import load_workspace
from kiro_conduit.merge import MergeOrchestrator
from kiro_conduit.orchestrator import ParallelOrchestrator
from kiro_conduit.roles.coordinator import CoordinatorOutcome
from kiro_conduit.types import LayerResult, TaskResult, VerifyLayer, VerifyResult

TASK_IDS = [f"t{i:02d}" for i in range(1, 18)]


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, capture_output=True)
    (path / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "work"], cwd=path, check=True, capture_output=True)


def _passing_outcome(task_id: str) -> CoordinatorOutcome:
    tr = TaskResult(task_id=task_id, success=True, diff="", files_changed=[])
    vr = VerifyResult(
        task_id=task_id,
        passed=True,
        layers=[LayerResult(layer=VerifyLayer.STATIC, passed=True, output="ok")],
        feedback="ok",
    )
    return CoordinatorOutcome(
        task_id=task_id,
        passed=True,
        attempts=1,
        last_task_result=tr,
        last_verify_result=vr,
        history=[(tr, vr)],
    )


def _build_large_dag_yaml() -> str:
    waves: list[tuple[str, str, list[str]]] = [
        ("w1", "serial", ["t01"]),
        ("w2", "parallel", ["t02", "t03", "t04"]),
        ("w3", "parallel", ["t05", "t06"]),
        ("w4", "parallel", ["t07", "t08", "t09"]),
        ("w5", "parallel", ["t10", "t11"]),
        ("w6", "parallel", ["t12", "t13", "t14"]),
        ("w7", "parallel", ["t15", "t16"]),
        ("w8", "serial", ["t17"]),
    ]
    deps_map = {
        "t02": ["t01"],
        "t03": ["t01"],
        "t04": ["t01"],
        "t05": ["t02", "t03"],
        "t06": ["t03", "t04"],
        "t07": ["t05"],
        "t08": ["t05", "t06"],
        "t09": ["t06"],
        "t10": ["t07", "t08"],
        "t11": ["t08", "t09"],
        "t12": ["t10"],
        "t13": ["t10", "t11"],
        "t14": ["t11"],
        "t15": ["t12", "t13"],
        "t16": ["t13", "t14"],
        "t17": ["t15", "t16"],
    }
    lines = ["phases:"]
    for name, wave_type, tasks in waves:
        lines.append(f"  - name: {name}")
        lines.append(f"    type: {wave_type}")
        lines.append(f"    tasks: [{', '.join(tasks)}]")
    lines.append("tasks:")
    lines.append('  t01: {spec: specs/t01.md, files_owned: ["t01.py"]}')
    for tid in TASK_IDS[1:]:
        deps = ", ".join(deps_map[tid])
        lines.append(
            f'  {tid}: {{spec: specs/{tid}.md, depends_on: [{deps}], '
            f'files_owned: ["{tid}.py"]}}'
        )
    lines.append("shared_files: []")
    return "\n".join(lines) + "\n"


@pytest.mark.asyncio
async def test_large_spec_eight_wave_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "dag.yaml").write_text(_build_large_dag_yaml(), encoding="utf-8")
    specs = ws_dir / "specs"
    specs.mkdir()
    for tid in TASK_IDS:
        (specs / f"{tid}.md").write_text(f"spec for {tid}\n")

    ws = load_workspace(ws_dir / "dag.yaml")
    assert len(ws.tasks) == 17
    assert len(ws.phases) == 8

    async def fake(self, task_def, wm, lock_manager, sem, base_branch, owner_handles=None):  # type: ignore[no-untyped-def]
        async with sem:
            wt = await wm.create(task_def.id, base_branch=base_branch)
            owned = task_def.files_owned[0] if task_def.files_owned else f"{task_def.id}.py"
            (wt.path / owned).write_text(f"// {task_def.id}\n")
            return _passing_outcome(task_def.id)

    monkeypatch.setattr(ParallelOrchestrator, "_run_one_task", fake)

    report = await ParallelOrchestrator(ws, base_repo=repo, max_concurrency=4).run()
    assert report.all_passed
    assert set(report.outcomes) == set(TASK_IDS)
    assert report.passed_count == 17
    assert report.failed_count == 0

    successful = {tid for tid, out in report.outcomes.items() if out.passed}
    merge_report = await MergeOrchestrator(ws, base_repo=repo).merge(
        handles=report.handles, successful_task_ids=successful
    )
    assert merge_report.all_merged

    for tid in TASK_IDS:
        owned = ws.tasks[tid].files_owned[0]
        show = subprocess.run(
            ["git", "show", f"main:{owned}"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert show.returncode == 0, f"missing {owned} on main after merge"
