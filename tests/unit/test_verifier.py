"""单元测试：Verifier 流水线。

跑真 shell（echo / false / sleep）但不调 kiro-cli。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kiro_conduit.roles.verifier import Verifier
from kiro_conduit.types import Task, TaskResult, VerifyLayer


def _make_task(cwd: Path, acceptance: list[str]) -> Task:
    return Task(id="t1", prompt="dummy", cwd=cwd, acceptance=acceptance)


def _make_success_result() -> TaskResult:
    return TaskResult(
        task_id="t1",
        success=True,
        diff="x",
        files_changed=["a.py"],
    )


class TestVerifyEnvInjection:
    @pytest.mark.asyncio
    async def test_injected_env_reaches_command(self, tmp_path: Path) -> None:
        """注入的 env 应能被验证命令读到。"""
        task = Task(
            id="t1", prompt="d", cwd=tmp_path,
            acceptance=['test "$KC_X" = "42"'], env={"KC_X": "42"},
        )
        result = await Verifier().verify(task, _make_success_result())
        assert result.passed

    @pytest.mark.asyncio
    async def test_without_env_same_command_fails(self, tmp_path: Path) -> None:
        """不注入时同一命令失败，证明确实靠注入（而非环境里本就有）。"""
        task = Task(
            id="t1", prompt="d", cwd=tmp_path,
            acceptance=['test "$KC_X" = "42"'], env={},
        )
        result = await Verifier().verify(task, _make_success_result())
        assert not result.passed


class TestClassify:
    def test_pytest_goes_to_dynamic(self) -> None:
        static, dynamic = Verifier._classify(["ruff check .", "pytest -q"])
        assert static == ["ruff check ."]
        assert dynamic == ["pytest -q"]

    def test_unittest_goes_to_dynamic(self) -> None:
        _static, dynamic = Verifier._classify(["python -m unittest"])
        assert dynamic == ["python -m unittest"]

    def test_npm_test_goes_to_dynamic(self) -> None:
        _static, dynamic = Verifier._classify(["npm test"])
        assert dynamic == ["npm test"]

    def test_other_commands_default_to_static(self) -> None:
        static, dynamic = Verifier._classify(["echo hi", "ls"])
        assert static == ["echo hi", "ls"]
        assert dynamic == []


class TestVerifyHappyPath:
    @pytest.mark.asyncio
    async def test_no_acceptance_all_skipped(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, [])
        result = await Verifier().verify(task, _make_success_result())
        assert result.passed
        assert all(layer.skipped for layer in result.layers)

    @pytest.mark.asyncio
    async def test_passing_static_only(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, ["true"])
        result = await Verifier().verify(task, _make_success_result())
        assert result.passed
        static = next(layer for layer in result.layers if layer.layer == VerifyLayer.STATIC)
        assert static.passed and not static.skipped
        dynamic = next(layer for layer in result.layers if layer.layer == VerifyLayer.DYNAMIC)
        assert dynamic.skipped

    @pytest.mark.asyncio
    async def test_passing_static_and_dynamic(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, ["true", "pytest --version"])
        result = await Verifier().verify(task, _make_success_result())
        assert result.passed


class TestVerifyShortCircuit:
    @pytest.mark.asyncio
    async def test_static_failure_skips_dynamic(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, ["false", "pytest --version"])
        result = await Verifier().verify(task, _make_success_result())
        assert not result.passed
        assert result.failed_layer == VerifyLayer.STATIC
        dynamic = next(layer for layer in result.layers if layer.layer == VerifyLayer.DYNAMIC)
        assert dynamic.skipped, "dynamic layer must be skipped when static fails"

    @pytest.mark.asyncio
    async def test_dynamic_failure(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, ["true", "pytest --invalid-arg-xxx"])
        result = await Verifier().verify(task, _make_success_result())
        assert not result.passed
        assert result.failed_layer == VerifyLayer.DYNAMIC


class TestVerifyImplementorFailure:
    @pytest.mark.asyncio
    async def test_skips_when_implementor_failed(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, ["true"])
        bad_result = TaskResult(
            task_id="t1",
            success=False,
            diff="",
            files_changed=[],
            error="boom",
        )
        result = await Verifier().verify(task, bad_result)
        assert not result.passed
        assert "boom" in result.feedback
        # 没跑任何层
        assert result.layers == []


class TestVerifyTimeout:
    @pytest.mark.asyncio
    async def test_long_command_times_out(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, [f"{sys.executable} -c 'import time; time.sleep(10)'"])
        verifier = Verifier(command_timeout=0.5)
        result = await verifier.verify(task, _make_success_result())
        assert not result.passed
        # 超时被归为 STATIC 失败（命令不含 pytest 等关键字）
        assert result.failed_layer == VerifyLayer.STATIC


class TestSemanticLayer:
    """M1.1 step 2: Layer 3 AI 语义 review。用 fake reviewer，不调真 LLM。"""

    @pytest.mark.asyncio
    async def test_no_reviewer_layer_skipped(self, tmp_path: Path) -> None:
        """默认 reviewer=None → Layer 3 skipped。"""
        task = _make_task(tmp_path, [])
        result = await Verifier().verify(task, _make_success_result())
        sem = next(layer for layer in result.layers if layer.layer == VerifyLayer.SEMANTIC)
        assert sem.skipped
        assert sem.passed

    @pytest.mark.asyncio
    async def test_reviewer_passes(self, tmp_path: Path) -> None:
        from kiro_conduit.semantic import NoOpSemanticReviewer

        task = _make_task(tmp_path, [])
        verifier = Verifier(semantic_reviewer=NoOpSemanticReviewer())
        result = await verifier.verify(task, _make_success_result())
        assert result.passed
        sem = next(layer for layer in result.layers if layer.layer == VerifyLayer.SEMANTIC)
        assert sem.passed
        assert not sem.skipped

    @pytest.mark.asyncio
    async def test_reviewer_fails(self, tmp_path: Path) -> None:
        from kiro_conduit.semantic import ReviewContext, ReviewResult

        class _AlwaysFail:
            async def review(self, ctx: ReviewContext) -> ReviewResult:
                return ReviewResult(passed=False, feedback="too sloppy")

        task = _make_task(tmp_path, [])
        verifier = Verifier(semantic_reviewer=_AlwaysFail())
        result = await verifier.verify(task, _make_success_result())
        assert not result.passed
        assert result.failed_layer == VerifyLayer.SEMANTIC
        assert "too sloppy" in result.feedback

    @pytest.mark.asyncio
    async def test_semantic_skipped_when_static_failed(self, tmp_path: Path) -> None:
        """Earlier layer 挂了 Layer 3 不该跑。"""
        from kiro_conduit.semantic import ReviewContext, ReviewResult

        called = False

        class _Spy:
            async def review(self, ctx: ReviewContext) -> ReviewResult:
                nonlocal called
                called = True
                return ReviewResult(passed=True, feedback="")

        # static 层故意挂
        task = _make_task(tmp_path, ["false"])
        verifier = Verifier(semantic_reviewer=_Spy())
        result = await verifier.verify(task, _make_success_result())
        assert result.failed_layer == VerifyLayer.STATIC
        sem = next(layer for layer in result.layers if layer.layer == VerifyLayer.SEMANTIC)
        assert sem.skipped
        assert not called  # reviewer 根本没被调

    @pytest.mark.asyncio
    async def test_semantic_failure_skips_contract(self, tmp_path: Path) -> None:
        """Layer 3 挂了 Layer 4 也该 skip。"""
        from kiro_conduit.semantic import ReviewContext, ReviewResult

        class _AlwaysFail:
            async def review(self, ctx: ReviewContext) -> ReviewResult:
                return ReviewResult(passed=False, feedback="bad")

        # 同时传 contract baseline
        baseline = "def f() -> None: ...\n"
        (tmp_path / "lib.py").write_text("def f() -> None: pass\n", encoding="utf-8")
        task = _make_task(tmp_path, [])
        verifier = Verifier(
            semantic_reviewer=_AlwaysFail(),
            contract_baselines={"lib.py": baseline},
        )
        result = await verifier.verify(task, _make_success_result())
        assert result.failed_layer == VerifyLayer.SEMANTIC
        contract = next(layer for layer in result.layers if layer.layer == VerifyLayer.CONTRACT)
        assert contract.skipped


class TestContractLayer:
    """M1.1: Layer 4 接口契约校验。"""

    @pytest.mark.asyncio
    async def test_no_baselines_layer_skipped(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, [])
        result = await Verifier().verify(task, _make_success_result())
        contract_layer = next(
            layer for layer in result.layers if layer.layer == VerifyLayer.CONTRACT
        )
        assert contract_layer.skipped
        assert contract_layer.passed

    @pytest.mark.asyncio
    async def test_consumer_keeps_signature(self, tmp_path: Path) -> None:
        baseline = "def add(a: int, b: int) -> int: ...\n"
        # consumer 实现了函数体但保持签名
        (tmp_path / "lib.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        task = _make_task(tmp_path, [])
        verifier = Verifier(contract_baselines={"lib.py": baseline})
        result = await verifier.verify(task, _make_success_result())
        assert result.passed
        contract_layer = next(
            layer for layer in result.layers if layer.layer == VerifyLayer.CONTRACT
        )
        assert contract_layer.passed
        assert not contract_layer.skipped

    @pytest.mark.asyncio
    async def test_consumer_changes_signature_fails(self, tmp_path: Path) -> None:
        baseline = "def add(a: int, b: int) -> int: ...\n"
        # consumer 偷加了一个参数
        (tmp_path / "lib.py").write_text(
            "def add(a: int, b: int, *, signed: bool = True) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        task = _make_task(tmp_path, [])
        verifier = Verifier(contract_baselines={"lib.py": baseline})
        result = await verifier.verify(task, _make_success_result())
        assert not result.passed
        assert result.failed_layer == VerifyLayer.CONTRACT
        assert "lib.py" in result.feedback

    @pytest.mark.asyncio
    async def test_baseline_file_missing_in_consumer(self, tmp_path: Path) -> None:
        """consumer 把 stub 文件删了。"""
        baseline = "def add(a: int, b: int) -> int: ...\n"
        # 不创建 lib.py
        task = _make_task(tmp_path, [])
        verifier = Verifier(contract_baselines={"lib.py": baseline})
        result = await verifier.verify(task, _make_success_result())
        assert not result.passed
        assert result.failed_layer == VerifyLayer.CONTRACT
        assert "missing" in result.feedback.lower()

    @pytest.mark.asyncio
    async def test_contract_skipped_when_earlier_layer_fails(self, tmp_path: Path) -> None:
        """earlier layer 挂了就不跑 contract。"""
        baseline = "def add(a: int, b: int) -> int: ...\n"
        (tmp_path / "lib.py").write_text(
            "def add(a: int, b: int) -> int: return a + b\n",
            encoding="utf-8",
        )
        # static 层故意挂
        task = _make_task(tmp_path, ["false"])
        verifier = Verifier(contract_baselines={"lib.py": baseline})
        result = await verifier.verify(task, _make_success_result())
        assert not result.passed
        assert result.failed_layer == VerifyLayer.STATIC
        contract_layer = next(
            layer for layer in result.layers if layer.layer == VerifyLayer.CONTRACT
        )
        assert contract_layer.skipped
