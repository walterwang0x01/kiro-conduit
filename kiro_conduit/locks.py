"""共享文件锁：M1.0 只实现 single-writer policy。

设计：
- 一个 SharedFileLockManager 管所有 shared file 的锁
- 同一时刻一个文件最多 1 个 task 持有
- 用 asyncio.Lock 实现进程内互斥（M1.0 单进程足够；M1.1+ 跨进程再升级）
- 持锁后写一个 .lock 文件到 .kiro-conduit/locks/，记录 task_id + 时间戳，方便排错

接口：
    lm = SharedFileLockManager(workspace, base_repo)
    async with lm.acquire("src/x.py", "task-a"):
        # 在 task-a 的 worktree 里写 src/x.py
        ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from kiro_conduit.dag import SharedFilePolicy, Workspace

logger = logging.getLogger(__name__)


LOCKS_SUBDIR = ".kiro-conduit/locks"


class LockError(RuntimeError):
    """锁操作失败。"""


@dataclass(frozen=True, slots=True)
class LockRecord:
    """锁文件内容（持久化到 .lock 文件，便于排错）。"""

    task_id: str
    file_path: str
    acquired_at: float  # unix timestamp


@dataclass
class _FileLock:
    """单个文件的锁状态。"""

    aio_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    holder: str | None = None  # 当前持锁的 task_id（None = 未被持有）


class SharedFileLockManager:
    """所有共享文件的锁管理器。"""

    def __init__(self, workspace: Workspace, base_repo: Path) -> None:
        if not base_repo.is_absolute():
            raise ValueError(f"base_repo must be absolute, got {base_repo}")
        self._workspace = workspace
        self._base_repo = base_repo
        # 只为声明过的 shared_files 准备锁
        self._locks: dict[str, _FileLock] = {
            sf.path: _FileLock() for sf in workspace.shared_files
        }
        self._locks_dir = base_repo / LOCKS_SUBDIR
        self._locks_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def acquire(self, file_path: str, task_id: str) -> AsyncIterator[None]:
        """获取共享文件的锁。退出 context 时自动释放。

        如果 file_path 没在 dag 的 shared_files 声明，抛 LockError——这是配置错误，
        不能默默放过。
        """
        sf = self._workspace.shared_file(file_path)
        if sf is None:
            raise LockError(
                f"file {file_path!r} is not a declared shared_file; "
                "task should not be requesting a lock on it"
            )
        if sf.policy != SharedFilePolicy.SINGLE_WRITER:
            # M1.0 限制
            raise LockError(
                f"shared file {file_path!r} has policy {sf.policy.value!r}, "
                "only single-writer is supported in M1.0"
            )

        flock = self._locks[file_path]
        logger.debug(
            "[lock] task=%s acquiring %s (current holder=%s)",
            task_id,
            file_path,
            flock.holder,
        )
        await flock.aio_lock.acquire()
        try:
            flock.holder = task_id
            self._write_lock_file(file_path, task_id)
            logger.info("[lock] task=%s acquired %s", task_id, file_path)
            yield
        finally:
            flock.holder = None
            self._remove_lock_file(file_path)
            flock.aio_lock.release()
            logger.info("[lock] task=%s released %s", task_id, file_path)

    def current_holder(self, file_path: str) -> str | None:
        """查询当前持锁者（无锁查询，仅用于调试 / dashboard）。"""
        flock = self._locks.get(file_path)
        return flock.holder if flock else None

    # ------------------------------------------------------------ internal

    def _lock_file_path(self, shared_path: str) -> Path:
        # 把路径分隔符转义成 __，避免子目录搞乱 .kiro-conduit/locks/ 结构
        safe = shared_path.replace("/", "__").replace("\\", "__")
        return self._locks_dir / f"{safe}.lock"

    def _write_lock_file(self, shared_path: str, task_id: str) -> None:
        record = LockRecord(
            task_id=task_id,
            file_path=shared_path,
            acquired_at=time.time(),
        )
        try:
            self._lock_file_path(shared_path).write_text(
                json.dumps(
                    {
                        "task_id": record.task_id,
                        "file_path": record.file_path,
                        "acquired_at": record.acquired_at,
                    }
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            # 写 .lock 文件失败不致命（仅用于排错），warn 一下
            logger.warning("[lock] failed to write lock file: %s", exc)

    def _remove_lock_file(self, shared_path: str) -> None:
        try:
            self._lock_file_path(shared_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[lock] failed to remove lock file: %s", exc)
