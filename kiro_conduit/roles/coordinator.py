"""Coordinator 角色：调度 Implementor → Verifier，处理重试。

CIV 三角色之一。本角色的边界：
- 输入：一个或多个 Task
- 输出：每个 Task 的最终 VerifyResult
- 不做：写代码（read-only），不做 merge

M0 实现：
- 单任务串行（多任务 / DAG / 并行 → M1）
- Verifier 失败时把 feedback 注入 prompt 重试
- 最多 3 次重试（VeriMAP 论文默认值）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from kiro_conduit.roles.implementor import Implementor
from kiro_conduit.roles.verifier import Verifier
from kiro_conduit.types import Task, TaskResult, VerifyResult

logger = logging.getLogger(__name__)

# 重试成功时的回调签名：(task_id, failed_feedback, failed_layer, attempts)
RetrySuccessCallback = Callable[[str, str, str | None, int], None]


@dataclass(frozen=True, slots=True)
class CoordinatorOutcome:
    """单任务执行的最终结果。"""

    task_id: str
    passed: bool
    attempts: int
    last_task_result: TaskResult
    last_verify_result: VerifyResult
    history: list[tuple[TaskResult, VerifyResult]] = field(default_factory=list)


class Coordinator:
    """串行调度一个任务（M0），含重试。"""

    def __init__(
        self,
        implementor: Implementor,
        verifier: Verifier,
        max_attempts: int = 3,
        on_retry_success: RetrySuccessCallback | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._implementor = implementor
        self._verifier = verifier
        self._max_attempts = max_attempts
        self._on_retry_success = on_retry_success

    async def run_task(self, task: Task) -> CoordinatorOutcome:
        """跑一个任务，返回最终结果。"""
        history: list[tuple[TaskResult, VerifyResult]] = []
        current_task = task

        for attempt in range(1, self._max_attempts + 1):
            logger.info(
                "[coordinator] task=%s attempt=%d/%d",
                task.id,
                attempt,
                self._max_attempts,
            )

            task_result = await self._implementor.run(current_task)
            verify_result = await self._verifier.verify(current_task, task_result)
            history.append((task_result, verify_result))

            if verify_result.passed:
                logger.info("[coordinator] task=%s PASSED on attempt %d", task.id, attempt)
                # 如果是重试后才过的（attempt > 1），触发 nudge 回调
                if attempt > 1 and self._on_retry_success:
                    _, prev_verify_result = history[-2]
                    failed_layer = (
                        str(prev_verify_result.failed_layer)
                        if prev_verify_result.failed_layer
                        else None
                    )
                    try:
                        self._on_retry_success(
                            task.id,
                            prev_verify_result.feedback,
                            failed_layer,
                            attempt,
                        )
                    except Exception:
                        # nudge 失败不应阻塞主流程
                        logger.warning(
                            "[coordinator] on_retry_success callback failed",
                            exc_info=True,
                        )
                return CoordinatorOutcome(
                    task_id=task.id,
                    passed=True,
                    attempts=attempt,
                    last_task_result=task_result,
                    last_verify_result=verify_result,
                    history=history,
                )

            logger.warning(
                "[coordinator] task=%s attempt %d failed: %s",
                task.id,
                attempt,
                verify_result.feedback[:200],
            )

            if attempt < self._max_attempts:
                # 把反馈拼进 prompt 重试
                current_task = replace(
                    current_task,
                    prompt=self._build_retry_prompt(task, task_result, verify_result),
                )

        logger.error("[coordinator] task=%s exhausted retries", task.id)
        last_task, last_verify = history[-1]
        return CoordinatorOutcome(
            task_id=task.id,
            passed=False,
            attempts=self._max_attempts,
            last_task_result=last_task,
            last_verify_result=last_verify,
            history=history,
        )

    @staticmethod
    def _build_retry_prompt(
        original: Task,
        last_task_result: TaskResult,
        last_verify_result: VerifyResult,
    ) -> str:
        """带反馈的重试 prompt。"""
        parts = [
            "上一次实施没有通过验证，请根据反馈修正。",
            "",
            "原始任务说明：",
            original.prompt,
            "",
            f"上一次失败的层：{last_verify_result.failed_layer}",
            "",
            "Verifier 反馈：",
            last_verify_result.feedback,
        ]
        if last_task_result.files_changed:
            parts.extend(
                [
                    "",
                    "上一次改动的文件（已保留在工作区，可以继续修改）：",
                    *(f"  - {f}" for f in last_task_result.files_changed),
                ]
            )
        return "\n".join(parts)
