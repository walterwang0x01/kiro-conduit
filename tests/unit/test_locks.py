"""单元测试：SharedFileLockManager。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent

import pytest

from lwa_conduit.dag import load_workspace
from lwa_conduit.locks import LockError, SharedFileLockManager


def _make_workspace_with_shared_file(tmp_path: Path):
    body = dedent(
        """
        phases:
          - name: A
            type: parallel
            tasks: [t1, t2]
        tasks:
          t1:
            spec: s
            shared_files_to_modify: ["src/shared.py"]
          t2:
            spec: s
            shared_files_to_modify: ["src/shared.py"]
        shared_files:
          - path: src/shared.py
            policy: single-writer
        """
    ).lstrip()
    p = tmp_path / "dag.yaml"
    p.write_text(body, encoding="utf-8")
    return load_workspace(p)


class TestAcquire:
    @pytest.mark.asyncio
    async def test_acquire_release(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_shared_file(tmp_path)
        lm = SharedFileLockManager(ws, tmp_path)

        async with lm.acquire("src/shared.py", "t1"):
            assert lm.current_holder("src/shared.py") == "t1"
        assert lm.current_holder("src/shared.py") is None

    @pytest.mark.asyncio
    async def test_lock_file_created_then_removed(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_shared_file(tmp_path)
        lm = SharedFileLockManager(ws, tmp_path)

        lock_file = tmp_path / ".lwa-conduit" / "locks" / "src__shared.py.lock"

        async with lm.acquire("src/shared.py", "t1"):
            assert lock_file.exists()
        assert not lock_file.exists()

    @pytest.mark.asyncio
    async def test_two_tasks_serialize(self, tmp_path: Path) -> None:
        """两个 task 同时请求同一个文件锁，第二个等第一个释放。"""
        ws = _make_workspace_with_shared_file(tmp_path)
        lm = SharedFileLockManager(ws, tmp_path)

        order: list[str] = []

        async def hold(task_id: str, hold_seconds: float) -> None:
            async with lm.acquire("src/shared.py", task_id):
                order.append(f"acquired-{task_id}")
                await asyncio.sleep(hold_seconds)
                order.append(f"released-{task_id}")

        # 并发起两个，第一个持锁 0.05s，第二个应该等
        await asyncio.gather(
            hold("t1", 0.05),
            hold("t2", 0.01),
        )

        # 验证：t1 先 acquire 后 release，然后 t2 才 acquire
        assert order[0] == "acquired-t1"
        assert order[1] == "released-t1"
        assert order[2] == "acquired-t2"
        assert order[3] == "released-t2"

    @pytest.mark.asyncio
    async def test_lock_unknown_file_raises(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_shared_file(tmp_path)
        lm = SharedFileLockManager(ws, tmp_path)

        with pytest.raises(LockError, match="not a declared shared_file"):
            async with lm.acquire("src/nope.py", "t1"):
                pass

    @pytest.mark.asyncio
    async def test_lock_released_on_exception(self, tmp_path: Path) -> None:
        """持锁过程中抛异常时锁要被释放。"""
        ws = _make_workspace_with_shared_file(tmp_path)
        lm = SharedFileLockManager(ws, tmp_path)

        with pytest.raises(RuntimeError, match="boom"):
            async with lm.acquire("src/shared.py", "t1"):
                raise RuntimeError("boom")

        # 锁已释放
        assert lm.current_holder("src/shared.py") is None
        # 还能再获取
        async with lm.acquire("src/shared.py", "t2"):
            assert lm.current_holder("src/shared.py") == "t2"


class TestConstructor:
    def test_base_repo_must_be_absolute(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_shared_file(tmp_path)
        with pytest.raises(ValueError, match="absolute"):
            SharedFileLockManager(ws, Path("relative"))

    def test_locks_dir_created(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_shared_file(tmp_path)
        SharedFileLockManager(ws, tmp_path)
        assert (tmp_path / ".lwa-conduit" / "locks").is_dir()


# --- M1.1 step 3: 新 policy 测试 -------------------------------------------


def _make_workspace_with_policy(tmp_path: Path, policy: str):
    body = dedent(
        f"""
        phases:
          - name: A
            type: parallel
            tasks: [t1]
        tasks:
          t1:
            spec: s
            shared_files_to_modify: ["src/shared.py"]
        shared_files:
          - path: src/shared.py
            policy: {policy}
        """
    ).lstrip()
    p = tmp_path / "dag.yaml"
    p.write_text(body, encoding="utf-8")
    return load_workspace(p)


class TestAppendOnlyPolicy:
    @pytest.mark.asyncio
    async def test_append_passes(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_policy(tmp_path, "append-only")
        lm = SharedFileLockManager(ws, tmp_path)

        # 文件提前有内容
        target = tmp_path / "src" / "shared.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original\n", encoding="utf-8")

        async with lm.acquire("src/shared.py", "t1"):
            target.write_text("original\nappended\n", encoding="utf-8")
        # 不抛 = 通过

    @pytest.mark.asyncio
    async def test_append_violation_rejected(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_policy(tmp_path, "append-only")
        lm = SharedFileLockManager(ws, tmp_path)

        target = tmp_path / "src" / "shared.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original\n", encoding="utf-8")

        with pytest.raises(LockError, match="append-only violation"):
            async with lm.acquire("src/shared.py", "t1"):
                # 改了已有内容（不是追加）
                target.write_text("DIFFERENT\n", encoding="utf-8")

    @pytest.mark.asyncio
    async def test_append_two_tasks_serialize(self, tmp_path: Path) -> None:
        """append-only 仍然互斥（防 write 内部交错），但每个写都通过 prefix 校验。"""
        ws = _make_workspace_with_policy(tmp_path, "append-only")
        lm = SharedFileLockManager(ws, tmp_path)

        target = tmp_path / "src" / "shared.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("base\n", encoding="utf-8")

        async def append(task_id: str, line: str) -> None:
            async with lm.acquire("src/shared.py", task_id):
                cur = target.read_text(encoding="utf-8")
                target.write_text(cur + line + "\n", encoding="utf-8")

        await asyncio.gather(append("t1", "a"), append("t2", "b"))
        content = target.read_text(encoding="utf-8")
        assert content.startswith("base\n")
        assert "a" in content and "b" in content


class TestCoordinatorOnlyPolicy:
    @pytest.mark.asyncio
    async def test_task_acquire_rejected(self, tmp_path: Path) -> None:
        ws = _make_workspace_with_policy(tmp_path, "coordinator-only")
        lm = SharedFileLockManager(ws, tmp_path)

        with pytest.raises(LockError, match="coordinator-only"):
            async with lm.acquire("src/shared.py", "t1"):
                pass
