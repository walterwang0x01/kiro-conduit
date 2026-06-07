"""跨 run 仓库记忆：积累环境知识、失败模式、成功 plan 示例。

灵感来源：Hermes Agent 的分层记忆 + 自省 nudge 机制。
区别在于 kiro-conduit 是确定性编排器，记忆只作为**只读建议**注入 prompt，
绝不能自动改 DAG 或绕过 Verifier。

存储位置：`<base_repo>/.kiro-conduit/memory.json`
设计要点：
- 原子写（写临时文件再 replace），复用 run_state 的模式
- load 容错：文件不存在 / 损坏 / 版本不符都返回空 Memory
- 每类记忆有上限，超出时淘汰最旧的（bounded，不无限增长）
- 条目带 timestamp，方便排序和清理
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kiro_conduit.run_state import CONDUIT_DIR_NAME

MEMORY_FILENAME = "memory.json"
_SCHEMA_VERSION = 1

# 每类记忆的默认上限
MAX_ENV_LESSONS = 50
MAX_FAILURE_PATTERNS = 100
MAX_PLAN_EXAMPLES = 20


@dataclass(frozen=True, slots=True)
class EnvLesson:
    """环境知识：运行仓库时需要的特定配置/前置条件。"""

    description: str
    timestamp: float
    task_id: str | None = None  # 学到这条知识的 task（可选）

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "description": self.description,
            "timestamp": self.timestamp,
        }
        if self.task_id:
            d["task_id"] = self.task_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvLesson:
        return cls(
            description=str(data.get("description", "")),
            timestamp=float(data.get("timestamp", 0.0)),
            task_id=data.get("task_id"),
        )


@dataclass(frozen=True, slots=True)
class FailurePattern:
    """失败模式：某类 task 的常见首次失败根因和解决方式。

    confidence: 置信度（1-100）。初始 50，每次被后续 run 验证有效 +15，
    长时间没用到则由 Memory.decay_confidence() 衰减 -10。
    低于 CONFIDENCE_FLOOR 时被淘汰（比纯按时间淘汰更聪明）。
    """

    pattern: str  # 一句话描述失败模式
    root_cause: str  # 根因
    resolution: str  # 怎么修的
    timestamp: float
    task_id: str | None = None
    failed_layer: str | None = None  # static / dynamic / semantic / contract
    confidence: int = 50  # 初始置信度

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "pattern": self.pattern,
            "root_cause": self.root_cause,
            "resolution": self.resolution,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
        }
        if self.task_id:
            d["task_id"] = self.task_id
        if self.failed_layer:
            d["failed_layer"] = self.failed_layer
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailurePattern:
        return cls(
            pattern=str(data.get("pattern", "")),
            root_cause=str(data.get("root_cause", "")),
            resolution=str(data.get("resolution", "")),
            timestamp=float(data.get("timestamp", 0.0)),
            task_id=data.get("task_id"),
            failed_layer=data.get("failed_layer"),
            confidence=int(data.get("confidence", 50)),
        )


@dataclass(frozen=True, slots=True)
class PlanExample:
    """被验证有效的 plan 拆分示例（用作 planner 的 few-shot）。"""

    spec_summary: str  # spec 的简短摘要（不存全文，省空间）
    task_count: int
    task_ids: list[str]
    timestamp: float
    score: int = 0  # 自评得分

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_summary": self.spec_summary,
            "task_count": self.task_count,
            "task_ids": self.task_ids,
            "timestamp": self.timestamp,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanExample:
        raw_ids = data.get("task_ids", [])
        return cls(
            spec_summary=str(data.get("spec_summary", "")),
            task_count=int(data.get("task_count", 0)),
            task_ids=[str(x) for x in raw_ids] if isinstance(raw_ids, list) else [],
            timestamp=float(data.get("timestamp", 0.0)),
            score=int(data.get("score", 0)),
        )


@dataclass(slots=True)
class Memory:
    """跨 run 的仓库记忆。"""

    env_lessons: list[EnvLesson] = field(default_factory=list)
    failure_patterns: list[FailurePattern] = field(default_factory=list)
    plan_examples: list[PlanExample] = field(default_factory=list)

    # ---------------------------------------------------------------- 添加

    def add_env_lesson(
        self,
        description: str,
        task_id: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        """添加一条环境知识。重复内容（description 完全相同）不会重复添加。"""
        ts = timestamp if timestamp is not None else time.time()
        # 去重：同样 description 不重复存
        if any(e.description == description for e in self.env_lessons):
            return
        self.env_lessons.append(
            EnvLesson(description=description, timestamp=ts, task_id=task_id)
        )
        # 超出上限，淘汰最旧
        if len(self.env_lessons) > MAX_ENV_LESSONS:
            self.env_lessons.sort(key=lambda x: x.timestamp)
            self.env_lessons = self.env_lessons[-MAX_ENV_LESSONS:]

    def add_failure_pattern(
        self,
        pattern: str,
        root_cause: str,
        resolution: str,
        task_id: str | None = None,
        failed_layer: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        """添加一条失败模式。"""
        ts = timestamp if timestamp is not None else time.time()
        self.failure_patterns.append(
            FailurePattern(
                pattern=pattern,
                root_cause=root_cause,
                resolution=resolution,
                timestamp=ts,
                task_id=task_id,
                failed_layer=failed_layer,
            )
        )
        if len(self.failure_patterns) > MAX_FAILURE_PATTERNS:
            self.failure_patterns.sort(key=lambda x: x.timestamp)
            self.failure_patterns = self.failure_patterns[-MAX_FAILURE_PATTERNS:]

    def boost_confidence(self, pattern_substr: str, amount: int = 15) -> int:
        """给包含 pattern_substr 的 failure pattern 加 confidence。返回被 boost 的数量。

        用于：run 中某个 task 首次失败的根因和历史记录匹配时，
        验证了这条记忆"确实有用"，confidence 应提升。
        """
        boosted = 0
        updated: list[FailurePattern] = []
        for fp in self.failure_patterns:
            if pattern_substr in fp.root_cause or pattern_substr in fp.pattern:
                new_conf = min(100, fp.confidence + amount)
                updated.append(FailurePattern(
                    pattern=fp.pattern,
                    root_cause=fp.root_cause,
                    resolution=fp.resolution,
                    timestamp=fp.timestamp,
                    task_id=fp.task_id,
                    failed_layer=fp.failed_layer,
                    confidence=new_conf,
                ))
                boosted += 1
            else:
                updated.append(fp)
        self.failure_patterns = updated
        return boosted

    def decay_confidence(self, amount: int = 10, floor: int = 10) -> int:
        """对所有 failure patterns 衰减 confidence，淘汰低于 floor 的。

        调用时机：每次 run 开始时调一次（不是每 task），模拟"久不用则遗忘"。
        返回被淘汰的条目数。
        """
        surviving: list[FailurePattern] = []
        removed = 0
        for fp in self.failure_patterns:
            new_conf = fp.confidence - amount
            if new_conf < floor:
                removed += 1
                continue
            surviving.append(FailurePattern(
                pattern=fp.pattern,
                root_cause=fp.root_cause,
                resolution=fp.resolution,
                timestamp=fp.timestamp,
                task_id=fp.task_id,
                failed_layer=fp.failed_layer,
                confidence=new_conf,
            ))
        self.failure_patterns = surviving
        return removed

    def add_plan_example(
        self,
        spec_summary: str,
        task_ids: list[str],
        score: int = 0,
        timestamp: float | None = None,
    ) -> None:
        """添加一条被验证有效的 plan 示例。"""
        ts = timestamp if timestamp is not None else time.time()
        self.plan_examples.append(
            PlanExample(
                spec_summary=spec_summary,
                task_count=len(task_ids),
                task_ids=task_ids,
                timestamp=ts,
                score=score,
            )
        )
        if len(self.plan_examples) > MAX_PLAN_EXAMPLES:
            self.plan_examples.sort(key=lambda x: x.timestamp)
            self.plan_examples = self.plan_examples[-MAX_PLAN_EXAMPLES:]

    # ---------------------------------------------------------------- 查询

    def get_failure_patterns_text(self, limit: int = 5) -> str:
        """返回最高 confidence 的 N 条失败模式的可读文本（用于注入 planner prompt）。"""
        if not self.failure_patterns:
            return ""
        # 按 confidence 降序，同分按时间降序
        top = sorted(
            self.failure_patterns,
            key=lambda x: (x.confidence, x.timestamp),
            reverse=True,
        )[:limit]
        lines: list[str] = []
        for fp in top:
            lines.append(
                f"- 模式: {fp.pattern} (置信度={fp.confidence})\n"
                f"  根因: {fp.root_cause}\n"
                f"  解法: {fp.resolution}"
            )
        return "\n".join(lines)

    def get_plan_examples_text(self, limit: int = 3) -> str:
        """返回最近 N 条 plan 示例的可读文本（用于注入 planner prompt）。"""
        if not self.plan_examples:
            return ""
        # 按分数降序，同分按时间降序
        best = sorted(
            self.plan_examples,
            key=lambda x: (x.score, x.timestamp),
            reverse=True,
        )[:limit]
        lines: list[str] = []
        for pe in best:
            lines.append(
                f"- spec: {pe.spec_summary}\n"
                f"  拆成 {pe.task_count} 个 task: {pe.task_ids}\n"
                f"  自评得分: {pe.score}"
            )
        return "\n".join(lines)

    # ---------------------------------------------------------------- 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _SCHEMA_VERSION,
            "env_lessons": [e.to_dict() for e in self.env_lessons],
            "failure_patterns": [f.to_dict() for f in self.failure_patterns],
            "plan_examples": [p.to_dict() for p in self.plan_examples],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        if data.get("version") != _SCHEMA_VERSION:
            raise ValueError(
                f"unsupported memory version: {data.get('version')!r}"
            )
        mem = cls()
        for e in data.get("env_lessons", []):
            if isinstance(e, dict):
                mem.env_lessons.append(EnvLesson.from_dict(e))
        for f in data.get("failure_patterns", []):
            if isinstance(f, dict):
                mem.failure_patterns.append(FailurePattern.from_dict(f))
        for p in data.get("plan_examples", []):
            if isinstance(p, dict):
                mem.plan_examples.append(PlanExample.from_dict(p))
        return mem


# ---------------------------------------------------------------- IO

def memory_path(base_repo: Path) -> Path:
    """memory.json 的标准路径。"""
    return base_repo / CONDUIT_DIR_NAME / MEMORY_FILENAME


def save_memory(path: Path, memory: Memory) -> None:
    """原子写：先写 .tmp 再 replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(memory.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_memory(path: Path) -> Memory:
    """读 memory。文件不存在 / 损坏 / 版本不符都返回空 Memory（不阻塞主流程）。"""
    if not path.is_file():
        return Memory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Memory.from_dict(data)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
        return Memory()


# ---------------------------------------------------------------- Nudge 回调

def make_retry_success_nudge(
    memory: Memory,
    persist_path: Path | None = None,
) -> Callable[[str, str, str | None, int], None]:
    """创建一个 on_retry_success 回调，在 Verifier 失败→重试成功时提炼教训。

    这是 Hermes-style 的"nudge"机制落地到 kiro-conduit：
    - 不调 LLM（保持轻量），直接从 feedback 文本提取模式
    - 存进 memory.failure_patterns
    - 可选地立即持久化到 persist_path

    参数：
      memory: 当前 Memory 实例（会被 in-place 修改）
      persist_path: 如果提供，每次添加后立即写盘
    """

    def _nudge(
        task_id: str,
        failed_feedback: str,
        failed_layer: str | None,
        attempts: int,
    ) -> None:
        # 从 feedback 提取前 200 字符作为 pattern 摘要
        pattern = failed_feedback[:200].replace("\n", " ").strip()
        if not pattern:
            return
        memory.add_failure_pattern(
            pattern=f"task {task_id} 第 1 次失败（attempt {attempts} 时通过）",
            root_cause=pattern,
            resolution=f"经过 {attempts - 1} 次重试后通过",
            task_id=task_id,
            failed_layer=failed_layer,
        )
        if persist_path:
            save_memory(persist_path, memory)

    return _nudge
