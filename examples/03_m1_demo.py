#!/usr/bin/env python3
"""M1.0 端到端 demo：跑 examples/dags/m1-hello.yaml 全流程。

流程：
  1. 在 tmp 目录建一个 git repo（base_repo）+ 拷贝 dag.yaml + specs
  2. ParallelOrchestrator 跑全 DAG（pkg-base 串行 → pkg-mul + pkg-sub 并行）
  3. MergeOrchestrator 把所有成功 task 的分支串行 merge 回 main
  4. 在 main 上跑全套 pytest 验证一切都通

跑法：
    cd ~/PycharmProjects/kiro-conduit
    .venv/bin/python examples/03_m1_demo.py

注意：会真调 Kiro CLI，约 2-4 分钟（取决于网速 + LLM 速度）。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kiro_conduit.dag import load_workspace  # noqa: E402
from kiro_conduit.merge import MergeOrchestrator  # noqa: E402
from kiro_conduit.orchestrator import ParallelOrchestrator  # noqa: E402


def setup_demo_workspace() -> tuple[Path, Path]:
    """建临时 git repo + 拷贝 dag.yaml + specs，返回 (base_repo, dag_yaml_path)。"""
    workdir = Path(tempfile.mkdtemp(prefix="kiro-conduit-m1-demo-"))
    src_examples = ROOT / "examples"
    shutil.copytree(src_examples / "specs", workdir / "specs")

    # 把 dag.yaml 里的 ../specs/ 改回 specs/，因为我们把 specs 拷到了同级
    src_yaml = (src_examples / "dags" / "m1-hello.yaml").read_text(encoding="utf-8")
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
        f"{len(workspace.phases)} phases, "
        f"{len(workspace.shared_files)} shared file(s)"
    )
    print(f"  Tasks: {sorted(workspace.tasks)}")
    print()

    orchestrator = ParallelOrchestrator(
        workspace=workspace,
        base_repo=base_repo,
        max_concurrency=2,
        max_attempts=2,  # demo 省 token
    )

    # ── Phase 1: parallel orchestration ────────────────────────────────────
    print("=" * 70)
    print("Phase 1: ParallelOrchestrator running (Implementor + Verifier per task)")
    print("=" * 70)
    t0 = time.monotonic()
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

    # 失败就早退
    if not report.all_passed:
        print()
        print("✗ Not all tasks passed; skipping merge phase")
        print(f"Workdir kept at: {base_repo}")
        return 1

    # ── Phase 2: serial merge ──────────────────────────────────────────────
    print()
    print("=" * 70)
    print("Phase 2: MergeOrchestrator running (serial merge in topological order)")
    print("=" * 70)
    successful = {tid for tid, out in report.outcomes.items() if out.passed}
    merger = MergeOrchestrator(workspace, base_repo)
    t1 = time.monotonic()
    merge_report = await merger.merge(
        handles=report.handles,
        successful_task_ids=successful,
        base_branch="main",
        commit_messages={
            tid: f"feat({tid}): kiro-conduit M1.0 demo"
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

    # 看 main 上的文件
    src_calc = base_repo / "src" / "calc"
    if src_calc.is_dir():
        files = sorted(p.name for p in src_calc.iterdir() if p.is_file())
        print(f"  src/calc/ contents: {files}")
    init_py = src_calc / "__init__.py"
    if init_py.is_file():
        print("  __init__.py:")
        for line in init_py.read_text(encoding="utf-8").splitlines():
            print(f"    {line}")

    # 跑 pytest
    print()
    print("  Running pytest -q...")
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

    # M1.0 demo 成功标准：
    # - 全部 task PASS（验证并行 + 重试 + 锁工作）
    # - 至少有 task 成功 merge（验证 merge orchestrator）
    # - 文本冲突时停下（这是预期行为，不算失败）
    merged_count = sum(1 for r in merge_report.results.values() if r.merged)
    demo_passed = report.all_passed and merged_count >= 1 and pytest_result.returncode == 0
    if demo_passed and merge_report.stopped_at:
        print()
        print(
            f"Note: merge stopped at {merge_report.stopped_at} due to a real "
            "text conflict. This is the expected M1.0 behavior — automatic "
            "semantic merge is intentionally not attempted. Resolve manually "
            "and continue (M1.1 will add a CLI flow for this)."
        )
    return 0 if demo_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
