"""单元测试：memory 模块（跨 run 仓库记忆）。"""

from __future__ import annotations

import json
from pathlib import Path

from lwa_conduit.memory import (
    MAX_ENV_LESSONS,
    MAX_FAILURE_PATTERNS,
    MAX_PLAN_EXAMPLES,
    Memory,
    load_memory,
    make_retry_success_nudge,
    memory_path,
    save_memory,
)


class TestMemoryAddAndQuery:
    """Memory 数据结构的增删查。"""

    def test_add_env_lesson(self) -> None:
        mem = Memory()
        mem.add_env_lesson("需要设置 DATABASE_URL", task_id="setup")
        assert len(mem.env_lessons) == 1
        assert mem.env_lessons[0].description == "需要设置 DATABASE_URL"
        assert mem.env_lessons[0].task_id == "setup"

    def test_add_env_lesson_dedup(self) -> None:
        """相同 description 不重复添加。"""
        mem = Memory()
        mem.add_env_lesson("需要设置 DATABASE_URL")
        mem.add_env_lesson("需要设置 DATABASE_URL")
        assert len(mem.env_lessons) == 1

    def test_env_lesson_bounded(self) -> None:
        """超出上限时淘汰最旧的。"""
        mem = Memory()
        for i in range(MAX_ENV_LESSONS + 10):
            mem.add_env_lesson(f"lesson-{i}", timestamp=float(i))
        assert len(mem.env_lessons) == MAX_ENV_LESSONS
        # 最旧的被淘汰
        descriptions = {e.description for e in mem.env_lessons}
        assert "lesson-0" not in descriptions
        assert f"lesson-{MAX_ENV_LESSONS + 9}" in descriptions

    def test_add_failure_pattern(self) -> None:
        mem = Memory()
        mem.add_failure_pattern(
            pattern="consumer 改了 __init__.py",
            root_cause="contract 校验拒绝",
            resolution="重试时只改自己的文件",
            task_id="pkg-mul",
            failed_layer="contract",
        )
        assert len(mem.failure_patterns) == 1
        fp = mem.failure_patterns[0]
        assert fp.pattern == "consumer 改了 __init__.py"
        assert fp.failed_layer == "contract"

    def test_failure_pattern_bounded(self) -> None:
        mem = Memory()
        for i in range(MAX_FAILURE_PATTERNS + 5):
            mem.add_failure_pattern(
                pattern=f"p-{i}",
                root_cause=f"r-{i}",
                resolution=f"fix-{i}",
                timestamp=float(i),
            )
        assert len(mem.failure_patterns) == MAX_FAILURE_PATTERNS

    def test_add_plan_example(self) -> None:
        mem = Memory()
        mem.add_plan_example(
            spec_summary="用户认证模块",
            task_ids=["auth-base", "auth-oauth"],
            score=85,
        )
        assert len(mem.plan_examples) == 1
        pe = mem.plan_examples[0]
        assert pe.task_count == 2
        assert pe.score == 85

    def test_plan_example_bounded(self) -> None:
        mem = Memory()
        for i in range(MAX_PLAN_EXAMPLES + 3):
            mem.add_plan_example(
                spec_summary=f"spec-{i}",
                task_ids=[f"t-{i}"],
                timestamp=float(i),
            )
        assert len(mem.plan_examples) == MAX_PLAN_EXAMPLES

    def test_get_failure_patterns_text_empty(self) -> None:
        mem = Memory()
        assert mem.get_failure_patterns_text() == ""

    def test_get_failure_patterns_text(self) -> None:
        mem = Memory()
        mem.add_failure_pattern(
            pattern="consumer 越界",
            root_cause="改了 owner 的文件",
            resolution="加 contract 提示",
        )
        text = mem.get_failure_patterns_text()
        assert "consumer 越界" in text
        assert "根因" in text

    def test_get_plan_examples_text_sorted_by_score(self) -> None:
        mem = Memory()
        mem.add_plan_example("low", ["a"], score=50, timestamp=1.0)
        mem.add_plan_example("high", ["b", "c"], score=90, timestamp=2.0)
        text = mem.get_plan_examples_text(limit=1)
        # 应该返回高分的
        assert "high" in text
        assert "low" not in text


class TestMemoryPersistence:
    """save / load 持久化。"""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        mem = Memory()
        mem.add_env_lesson("设置 REDIS_URL")
        mem.add_failure_pattern("lint 失败", "缺少 type hint", "加上 type hint")
        mem.add_plan_example("支付模块", ["pay-base", "pay-webhook"], score=78)

        p = memory_path(tmp_path)
        save_memory(p, mem)

        loaded = load_memory(p)
        assert len(loaded.env_lessons) == 1
        assert loaded.env_lessons[0].description == "设置 REDIS_URL"
        assert len(loaded.failure_patterns) == 1
        assert len(loaded.plan_examples) == 1
        assert loaded.plan_examples[0].score == 78

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / ".lwa-conduit" / "memory.json"
        mem = load_memory(p)
        assert len(mem.env_lessons) == 0
        assert len(mem.failure_patterns) == 0

    def test_load_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / ".lwa-conduit" / "memory.json"
        p.parent.mkdir(parents=True)
        p.write_text("not json {{{", encoding="utf-8")
        mem = load_memory(p)
        assert len(mem.env_lessons) == 0

    def test_load_wrong_version_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / ".lwa-conduit" / "memory.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"version": 999}), encoding="utf-8")
        mem = load_memory(p)
        assert len(mem.env_lessons) == 0

    def test_memory_path_location(self, tmp_path: Path) -> None:
        p = memory_path(tmp_path)
        assert p == tmp_path / ".lwa-conduit" / "memory.json"


class TestRetrySuccessNudge:
    """make_retry_success_nudge 回调。"""

    def test_nudge_adds_failure_pattern(self) -> None:
        mem = Memory()
        nudge = make_retry_success_nudge(mem)
        nudge("pkg-auth", "[contract failed] 改了 __init__.py", "contract", 2)
        assert len(mem.failure_patterns) == 1
        fp = mem.failure_patterns[0]
        assert "pkg-auth" in fp.pattern
        assert "__init__.py" in fp.root_cause
        assert fp.failed_layer == "contract"

    def test_nudge_persists_when_path_given(self, tmp_path: Path) -> None:
        mem = Memory()
        p = memory_path(tmp_path)
        nudge = make_retry_success_nudge(mem, persist_path=p)
        nudge("task-x", "test_foo failed: AssertionError", "dynamic", 3)
        # 应该已写盘
        loaded = load_memory(p)
        assert len(loaded.failure_patterns) == 1

    def test_nudge_ignores_empty_feedback(self) -> None:
        mem = Memory()
        nudge = make_retry_success_nudge(mem)
        nudge("task-y", "", None, 2)
        assert len(mem.failure_patterns) == 0


class TestConfidenceScoring:
    """confidence scoring：boost / decay / 淘汰。"""

    def test_boost_confidence(self) -> None:
        mem = Memory()
        mem.add_failure_pattern(
            pattern="consumer 改了接口文件",
            root_cause="contract 校验拒绝: __init__.py 签名变了",
            resolution="不要改 owner 的文件",
        )
        assert mem.failure_patterns[0].confidence == 50
        boosted = mem.boost_confidence("__init__.py", amount=15)
        assert boosted == 1
        assert mem.failure_patterns[0].confidence == 65

    def test_boost_caps_at_100(self) -> None:
        mem = Memory()
        mem.add_failure_pattern("p", "root", "fix")
        # boost 到超过 100
        mem.boost_confidence("root", amount=80)
        assert mem.failure_patterns[0].confidence == 100

    def test_boost_no_match(self) -> None:
        mem = Memory()
        mem.add_failure_pattern("p", "unrelated root", "fix")
        boosted = mem.boost_confidence("xyz_no_match")
        assert boosted == 0
        assert mem.failure_patterns[0].confidence == 50

    def test_decay_confidence(self) -> None:
        mem = Memory()
        mem.add_failure_pattern("p1", "root1", "fix1")  # confidence=50
        mem.add_failure_pattern("p2", "root2", "fix2")  # confidence=50
        # boost p1 到 80
        mem.boost_confidence("root1", amount=30)
        assert mem.failure_patterns[0].confidence == 80

        # decay -10
        removed = mem.decay_confidence(amount=10, floor=10)
        assert removed == 0
        # p1: 80→70, p2: 50→40
        confs = {fp.pattern: fp.confidence for fp in mem.failure_patterns}
        assert confs["p1"] == 70
        assert confs["p2"] == 40

    def test_decay_removes_below_floor(self) -> None:
        mem = Memory()
        mem.add_failure_pattern("weak", "root", "fix")  # confidence=50
        # 连续 decay 直到掉到 floor 以下
        mem.decay_confidence(amount=20, floor=10)  # 50→30
        assert len(mem.failure_patterns) == 1
        mem.decay_confidence(amount=20, floor=10)  # 30→10, 不低于 floor, 保留
        assert len(mem.failure_patterns) == 1
        mem.decay_confidence(amount=5, floor=10)  # 10→5, 低于 floor, 淘汰
        assert len(mem.failure_patterns) == 0

    def test_get_failure_patterns_text_sorted_by_confidence(self) -> None:
        mem = Memory()
        mem.add_failure_pattern("low-conf", "root1", "fix1")
        mem.add_failure_pattern("high-conf", "root2", "fix2")
        mem.boost_confidence("root2", amount=30)  # high-conf → 80
        text = mem.get_failure_patterns_text(limit=1)
        assert "high-conf" in text
        assert "low-conf" not in text

    def test_roundtrip_preserves_confidence(self, tmp_path: Path) -> None:
        mem = Memory()
        mem.add_failure_pattern("p", "root", "fix")
        mem.boost_confidence("root", amount=25)  # → 75
        p = memory_path(tmp_path)
        save_memory(p, mem)
        loaded = load_memory(p)
        assert loaded.failure_patterns[0].confidence == 75
