"""并行编排器：按 DAG 波次调度 task，每波内并行跑。

设计要点（来自 ARCHITECTURE.md）：
- 输入：Workspace（已经过 dag.py 的 load_workspace 校验）
- 切波次：topological_waves(workspace)
- 每波：
  - 并行起 N 个 worker（每个 worker 一个 worktree + Implementor + Verifier + 重试）
  - asyncio.Semaphore 限并发，避免一波太宽把机器跑爆
- 任意 task 失败：默认继续跑同波其他 task，但下游波次（依赖失败 task 的）会自动跳过
- 最终返回 ParallelRunReport，含每个 task 的结果

M1.0 范围：
- 不做 merge（merge 是 step 5 的 MergeOrchestrator）
- 不做 stub-first 接口锁定（M1.1）
- 不做 dashboard / TUI（M1.1）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from kiro_conduit.dag import TaskDef, Workspace, topological_waves
from kiro_conduit.locks import SharedFileLockManager
from kiro_conduit.roles.coordinator import Coordinator, CoordinatorOutcome
from kiro_conduit.roles.implementor import Implementor
from kiro_conduit.roles.verifier import Verifier
from kiro_conduit.types import Task
from kiro_conduit.worktree import WorktreeHandle, WorktreeManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParallelRunReport:
    """一次 run_workspace 的总结报告。"""

    outcomes: dict[str, CoordinatorOutcome]
    skipped: tuple[str, ...]  # 因上游失败被跳过的 task ids
    handles: dict[str, WorktreeHandle]  # 每个 task 的 worktree（merge 阶段用）

    @property
    def all_passed(self) -> bool:
        return not self.skipped and all(o.passed for o in self.outcomes.values())

    @property
    def passed_count(self) -> int:
        return sum(1 for o in self.outcomes.values() if o.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for o in self.outcomes.values() if not o.passed)


class ParallelOrchestrator:
    """波次并行调度器。"""

    def __init__(
        self,
        workspace: Workspace,
        base_repo: Path,
        max_concurrency: int = 4,
        max_attempts: int = 3,
        kiro_cli_path: str = "kiro-cli",
        prompt_timeout: float = 600.0,
    ) -> None:
        if not base_repo.is_absolute():
            raise ValueError(f"base_repo must be absolute, got {base_repo}")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._workspace = workspace
        self._base_repo = base_repo
        self._max_concurrency = max_concurrency
        self._max_attempts = max_attempts
        self._kiro_cli_path = kiro_cli_path
        self._prompt_timeout = prompt_timeout

    async def run(self, base_branch: str = "main") -> ParallelRunReport:
        """跑全工作区：所有波次依次执行，波内并行。

        注意：worktree 不会被自动清理（merge 阶段还要用）。调用方通过返回的
        ParallelRunReport.handles 拿到所有 worktree，最后自己决定何时调
        await wm.cleanup_all() 或类似清理。
        """
        waves = topological_waves(self._workspace)
        logger.info(
            "[orchestrator] %d waves total: %s",
            len(waves),
            [len(w) for w in waves],
        )

        outcomes: dict[str, CoordinatorOutcome] = {}
        skipped: list[str] = []
        failed_tasks: set[str] = set()
        handles: dict[str, WorktreeHandle] = {}

        # 注意：不进 WorktreeManager 的 async-with，因为我们不希望它自动清理
        wm = WorktreeManager(self._base_repo)
        await wm.__aenter__()
        try:
            lock_manager = SharedFileLockManager(self._workspace, self._base_repo)
            sem = asyncio.Semaphore(self._max_concurrency)

            for wave_idx, wave in enumerate(waves, start=1):
                wave_skipped, wave_to_run = self._partition_wave(
                    wave, failed_tasks
                )
                skipped.extend(wave_skipped)
                if not wave_to_run:
                    logger.warning(
                        "[orchestrator] wave %d: all skipped due to upstream failures",
                        wave_idx,
                    )
                    continue

                logger.info(
                    "[orchestrator] wave %d/%d: running %s, skipping %s",
                    wave_idx,
                    len(waves),
                    wave_to_run,
                    wave_skipped or "[]",
                )

                # 并行跑这波
                wave_results = await asyncio.gather(
                    *(
                        self._run_one_task(
                            self._workspace.task(tid),
                            wm,
                            lock_manager,
                            sem,
                            base_branch,
                        )
                        for tid in wave_to_run
                    ),
                    return_exceptions=True,
                )

                for tid, result in zip(wave_to_run, wave_results, strict=True):
                    if isinstance(result, BaseException):
                        logger.exception(
                            "[orchestrator] task %s crashed: %s", tid, result
                        )
                        failed_tasks.add(tid)
                        outcomes[tid] = self._make_crash_outcome(tid, result)
                    else:
                        outcomes[tid] = result
                        if not result.passed:
                            failed_tasks.add(tid)

                # 收集这波的 handles（成功失败都收，调用方按需用）
                for tid in wave_to_run:
                    h = wm._handles.get(tid)
                    if h is not None:
                        handles[tid] = h
        except BaseException:
            # 异常时清理（防 worktree 泄漏）
            await wm.cleanup_all()
            await wm.__aexit__(None, None, None)
            raise

        # 正常路径：不清理，留给调用方
        return ParallelRunReport(
            outcomes=outcomes,
            skipped=tuple(skipped),
            handles=handles,
        )

    async def cleanup_handles(self, handles: dict[str, WorktreeHandle]) -> None:
        """显式清理 worktree（merge 完成后调用）。"""
        wm = WorktreeManager(self._base_repo)
        wm._handles = dict(handles)
        await wm.cleanup_all()

    # ------------------------------------------------------------ internal

    def _partition_wave(
        self, wave: list[str], failed_tasks: set[str]
    ) -> tuple[list[str], list[str]]:
        """把这波 task 分成"跳过"和"要跑"两组。

        跳过条件：task 的 effective_deps 里有 failed_tasks 命中的项。
        """
        skipped: list[str] = []
        to_run: list[str] = []
        for tid in wave:
            t = self._workspace.task(tid)
            if any(dep in failed_tasks for dep in t.depends_on):
                skipped.append(tid)
            else:
                to_run.append(tid)
        return skipped, to_run

    async def _run_one_task(
        self,
        task_def: TaskDef,
        wm: WorktreeManager,
        lock_manager: SharedFileLockManager,
        sem: asyncio.Semaphore,
        base_branch: str,
    ) -> CoordinatorOutcome:
        """单 task 全流程：起 worktree → Implementor → Verifier → 重试。

        worktree 不在这里清理（merge 阶段还要用，由 ParallelOrchestrator.run
        的调用方决定何时清）。
        """
        async with sem:
            wt = await wm.create(task_def.id, base_branch=base_branch)
            task = self._materialize_task(task_def, wt.path)
            coord = Coordinator(
                implementor=_LockAwareImplementor(
                    kiro_cli_path=self._kiro_cli_path,
                    prompt_timeout=self._prompt_timeout,
                    lock_manager=lock_manager,
                    shared_files=task_def.shared_files_to_modify,
                ),
                verifier=Verifier(),
                max_attempts=self._max_attempts,
            )
            return await coord.run_task(task)

    def _materialize_task(self, task_def: TaskDef, worktree_path: Path) -> Task:
        """TaskDef → Task：读 spec 文件填 prompt + 设 cwd 到 worktree。"""
        spec_path = self._workspace.workspace_root / task_def.spec
        if not spec_path.is_file():
            raise FileNotFoundError(f"spec file not found: {spec_path}")
        prompt = spec_path.read_text(encoding="utf-8")
        return Task(
            id=task_def.id,
            prompt=prompt,
            cwd=worktree_path,
            acceptance=list(task_def.acceptance),
        )

    @staticmethod
    def _make_crash_outcome(task_id: str, exc: BaseException) -> CoordinatorOutcome:
        """task 在 orchestrator 层崩溃时的兜底 outcome。"""
        from kiro_conduit.types import LayerResult, TaskResult, VerifyLayer, VerifyResult

        tr = TaskResult(
            task_id=task_id,
            success=False,
            diff="",
            files_changed=[],
            error=f"orchestrator crash: {type(exc).__name__}: {exc}",
        )
        vr = VerifyResult(
            task_id=task_id,
            passed=False,
            layers=[
                LayerResult(
                    layer=VerifyLayer.STATIC,
                    passed=False,
                    output=str(exc),
                )
            ],
            feedback=f"orchestrator crash: {exc}",
        )
        return CoordinatorOutcome(
            task_id=task_id,
            passed=False,
            attempts=0,
            last_task_result=tr,
            last_verify_result=vr,
            history=[(tr, vr)],
        )


# ---------------------------------------------------------------------------
# 锁感知的 Implementor 包装
# ---------------------------------------------------------------------------


class _LockAwareImplementor(Implementor):
    """在 Implementor 之外加一层：跑 prompt 前先抢所有需要的 shared file 锁。

    M1.0 简化策略：在 Implementor 整个 run 期间持锁，最大化简单性。
    （更好做法：只在 worker 真正写文件时持锁，但需要 Kiro 配合，M1.1 再优化。）
    """

    def __init__(
        self,
        *,
        kiro_cli_path: str,
        prompt_timeout: float,
        lock_manager: SharedFileLockManager,
        shared_files: tuple[str, ...],
    ) -> None:
        super().__init__(kiro_cli_path=kiro_cli_path, prompt_timeout=prompt_timeout)
        self._lock_manager = lock_manager
        self._shared_files = shared_files

    async def run(self, task: Task) -> object:  # type: ignore[override]
        # 对所有需要的 shared file 依次（按字典序去抖死锁）抢锁
        sorted_files = sorted(self._shared_files)
        return await self._with_locks(sorted_files, task)

    async def _with_locks(self, files_to_lock: list[str], task: Task) -> object:
        if not files_to_lock:
            return await super().run(task)
        head, *tail = files_to_lock
        async with self._lock_manager.acquire(head, task.id):
            return await self._with_locks(tail, task)
