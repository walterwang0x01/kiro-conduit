#!/usr/bin/env python3
"""端到端 demo：Coordinator → Implementor → Verifier 跑通一个最小任务。

Demo 任务：
    在一个临时 git repo 里写一个 `calc.py`，包含函数 `add(a, b) -> int`，
    并写一个 `test_calc.py` 用 pytest 测试 add。
    Verifier 跑 `pytest -q` 验证。

跑法：
    cd ~/PycharmProjects/lwa-conduit
    python examples/02_civ_hello.py

预期：
    - 临时目录被创建（自动清理）
    - Implementor 写出 calc.py + test_calc.py
    - Verifier 跑 pytest 通过
    - 退出码 0
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 让脚本能直接运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lwa_conduit.roles import Coordinator, Implementor, Verifier  # noqa: E402
from lwa_conduit.types import Task  # noqa: E402


def setup_test_repo() -> Path:
    """建一个临时 git repo，返回路径。"""
    workdir = Path(tempfile.mkdtemp(prefix="lwa-conduit-demo-"))
    # 初始化 git，加一个空 README 当 baseline commit
    subprocess.run(["git", "init", "-b", "main"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "demo@lwa-conduit.local"],
        cwd=workdir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "lwa-conduit demo"],
        cwd=workdir, check=True, capture_output=True,
    )
    (workdir / "README.md").write_text("# lwa-conduit demo\n")
    subprocess.run(["git", "add", "."], cwd=workdir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=workdir, check=True, capture_output=True,
    )
    return workdir


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    workdir = setup_test_repo()
    print(f"✓ Created test repo: {workdir}")
    print()

    task = Task(
        id="add-function",
        prompt=(
            "在当前目录下创建两个文件：\n"
            "1. `calc.py`，定义函数 `def add(a: int, b: int) -> int`，返回两数之和。\n"
            "2. `test_calc.py`，用 pytest 写至少 2 个测试用例覆盖 add 函数（含 0、负数等边界）。\n"
            "\n"
            "要求：\n"
            "- 代码风格要能通过 `python -m py_compile` 编译\n"
            "- 测试要能通过 `pytest -q` 执行\n"
        ),
        cwd=workdir,
        acceptance=[
            # Layer 1 STATIC: 编译通过
            "python -m py_compile calc.py test_calc.py",
            # Layer 2 DYNAMIC: pytest 通过
            "pytest -q test_calc.py",
        ],
    )

    coordinator = Coordinator(
        implementor=Implementor(),
        verifier=Verifier(),
        max_attempts=2,  # demo 用，省 token
    )

    try:
        outcome = await coordinator.run_task(task)
    finally:
        # 留着 workdir 让用户看产物，最后才清
        pass

    print()
    print("=" * 60)
    print(f"Task: {outcome.task_id}")
    print(f"Passed: {outcome.passed}")
    print(f"Attempts: {outcome.attempts}")
    print(f"Files changed: {outcome.last_task_result.files_changed}")
    print()
    print("Verifier layers:")
    for layer in outcome.last_verify_result.layers:
        marker = "✓" if layer.passed else "✗"
        skipped = " (skipped)" if layer.skipped else ""
        print(f"  {marker} {layer.layer}{skipped}")

    if not outcome.passed:
        print()
        print("Feedback:")
        print(outcome.last_verify_result.feedback)

    print()
    print(f"Workdir kept at: {workdir}")
    print(f"To clean up: rm -rf {workdir}")

    # 也可以直接清，简单点；但保留更便于人工检查
    # shutil.rmtree(workdir, ignore_errors=True)
    _ = shutil  # 避免 unused-import warning

    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
