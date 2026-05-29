#!/usr/bin/env python3
"""M1.1 stub-first demo：跑 examples/dags/m1-stub-first.yaml 全流程。

跟 examples/03_m1_demo.py 的区别：
  - DAG 多了 pkg-stub task，由 interface_lock 把 __init__.py 冻结
  - pkg-mul / pkg-sub 不再写 __init__.py，merge 时不会撞
  - 期望：3/4 task PASS + 4/4 merge 成功 + main pytest 全过

跑法：
    cd ~/PycharmProjects/kiro-conduit
    .venv/bin/python examples/04_m1_stub_first_demo.py

注意：会真调 Kiro CLI，约 3-5 分钟（4 个 task）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kiro_conduit.dag import load_workspace  # noqa: E402
from kiro_conduit.dashboard import Dashboard  # noqa: E402
from kiro_conduit.events import EventBus  # noqa: E402
from kiro_conduit.merge import MergeOrchestrator  # noqa: E402
from kiro_conduit.orchestrator import ParallelOrchestrator  # noqa: E402

DAG_FILE_NAME = "m1-stub-first.yaml"
USE_DASHBOARD = os.environ.get("KIRO_CONDUIT_DASHBOARD", "").lower() in ("1", "true", "yes")


def setup_demo_workspace() -> tuple[Path, Path]:
    workdir = Path(tempfile.mkdtemp(prefix="kiro-conduit-stubfirst-demo-"))
    src_examples = ROOT / "examples"
    shutil.copytree(src_examples / "specs", workdir / "specs")
    src_yaml = (src_examples / "dags" / DAG_FILE_NAME).read_text(encoding="utf-8")
    (workdir / "dag.yaml").write_text(
        src_yaml.replace("../specs/", "specs/"), encoding="utf-8"
    )

    subprocess.run(["git", "init", "-b", "main"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "demo@kiro-conduit.local"],
        cwd=workdir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "kiro-conduit demo"],
        cwd=workdir, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=workdir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial: dag.yaml + specs"],
        cwd=workdir, check=True, capture_output=True,
    )
    return workdir, workdir / "dag.yaml"


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    base_repo, dag_yaml = setup_demo_workspace()
    print(f"✓ Demo workspace: {base_repo}")

    workspace = load_workspace(dag_yaml)
    print(
        f"✓ Loaded DAG: {len(workspace.tasks)} tasks, "
        f"{len(workspace.phases)} phases"
    )
    locks = []
    for phase in workspace.phases:
        for lock in phase.interface_locks:
            locks.append(f"{lock.owner} owns {lock.file} for {list(lock.consumers)}")
    print(f"  Interface locks: {locks}")
    print(f"  Tasks: {sorted(workspace.tasks)}")
    print()

    bus = EventBus() if USE_DASHBOARD else None
    orchestrator = ParallelOrchestrator(
        workspace=workspace,
        base_repo=base_repo,
        max_concurrency=2,
        max_attempts=2,
        event_bus=bus,
    )

    # ── Phase 1: parallel orchestration ────────────────────────────────────
    print("=" * 70)
    print("Phase 1: ParallelOrchestrator (with stub-first interface lock)")
    print("=" * 70)
    t0 = time.monotonic()
    if USE_DASHBOARD and bus is not None:
        dashboard = Dashboard(workspace=workspace)
        dashboard.attach(bus)
        with dashboard.live():
            report = await orchestrator.run()
            # 给 dashboard 留点时间渲染最后一帧
            await asyncio.sleep(0.5)
    else:
        report = await orchestrator.run()
    parallel_dur = time.monotonic() - t0

    print()
    print(f"✓ Parallel phase done in {parallel_dur:.1f}s")
    for tid in sorted(workspace.tasks):
        out = report.outcomes.get(tid)
        if out is None:
            print(f"  - {tid}: (skipped, deps failed)")
        else:
            mark = "✓" if out.passed else "✗"
            files = len(out.last_task_result.files_changed)
            print(
                f"  {mark} {tid}: passed={out.passed}, "
                f"attempts={out.attempts}, files_changed={files}"
            )

    if not report.all_passed:
        print()
        print("✗ Not all tasks passed; skipping merge phase")
        print(f"Workdir kept at: {base_repo}")
        return 1

    # ── Phase 2: serial merge ──────────────────────────────────────────────
    print()
    print("=" * 70)
    print("Phase 2: MergeOrchestrator")
    print("=" * 70)
    successful = {tid for tid, out in report.outcomes.items() if out.passed}
    merger = MergeOrchestrator(workspace, base_repo, event_bus=bus)
    t1 = time.monotonic()
    merge_report = await merger.merge(
        handles=report.handles,
        successful_task_ids=successful,
        base_branch="main",
        commit_messages={
            tid: f"feat({tid}): kiro-conduit M1.1 stub-first demo"
            for tid in successful
        },
    )
    merge_dur = time.monotonic() - t1

    print()
    print(f"✓ Merge phase done in {merge_dur:.1f}s")
    for tid, mr in merge_report.results.items():
        mark = "✓" if mr.merged else "✗"
        err = f" — {mr.error}" if mr.error else ""
        print(f"  {mark} {tid}{err}")

    # ── Phase 3: 验证 main 分支 ────────────────────────────────────────────
    print()
    print("=" * 70)
    print("Phase 3: Verifying main branch")
    print("=" * 70)
    src_calc = base_repo / "src" / "calc"
    if src_calc.is_dir():
        files = sorted(p.name for p in src_calc.iterdir() if p.is_file())
        print(f"  src/calc/ contents: {files}")
    init_py = src_calc / "__init__.py"
    if init_py.is_file():
        print("  __init__.py:")
        for line in init_py.read_text(encoding="utf-8").splitlines():
            print(f"    {line}")

    print()
    print("  Running pytest -q on main...")
    pytest_result = subprocess.run(
        ["pytest", "-q"],
        cwd=base_repo,
        capture_output=True,
        text=True,
    )
    print(pytest_result.stdout)
    if pytest_result.stderr:
        print(pytest_result.stderr)

    print()
    print("=" * 70)
    print(
        f"Total: parallel={parallel_dur:.1f}s + merge={merge_dur:.1f}s "
        f"= {parallel_dur + merge_dur:.1f}s"
    )
    print(f"Workdir kept at: {base_repo}")
    print(f"To clean up: rm -rf {base_repo}")
    print("=" * 70)

    # M1.1 demo 成功标准更严格：
    # - 全部 task PASS
    # - 全部 merge 成功（不再有共享文件冲突）
    # - main pytest 通过
    demo_passed = (
        report.all_passed
        and merge_report.all_merged
        and pytest_result.returncode == 0
    )
    if demo_passed:
        print()
        print(
            "✓ M1.1 stub-first demo SUCCESS — interface lock prevented "
            "the conflict that broke the M1.0 demo."
        )
    return 0 if demo_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
