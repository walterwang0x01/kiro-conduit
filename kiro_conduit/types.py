"""任务与验证结果的数据类型。

CIV 三角色（Coordinator / Implementor / Verifier）共用本模块的类型作为数据契约。
M0 阶段保持最小：不做 DAG，不做依赖图，只支持单任务串行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class TaskStatus(StrEnum):
    """任务生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Task:
    """一个 Implementor 要执行的任务。

    M0 字段最小化：
    - id: 唯一标识，用于日志 / worktree 命名
    - prompt: 给 Implementor 的指令
    - cwd: Implementor 的工作目录（已是 worktree 路径，调用方负责创建）
    - acceptance: Verifier 验证的命令清单（M0 用 shell 命令字符串，M1 改成结构化）
    """

    id: str
    prompt: str
    cwd: Path
    acceptance: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # 验证命令的隔离环境变量

    def __post_init__(self) -> None:
        if not self.cwd.is_absolute():
            raise ValueError(f"task cwd must be absolute, got {self.cwd}")


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Implementor 的产出。"""

    task_id: str
    success: bool
    diff: str  # git diff 内容，可能为空
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None
    transcript: str = ""  # Implementor 的全部输出，方便调试
    no_changes: bool = False  # 没改任何文件（可能是依赖已做掉，由 verifier 判定真假）
    runtime_kind: str | None = None
    model: str | None = None


class VerifyLayer(StrEnum):
    """Verifier 流水线层名。"""

    STATIC = "static"
    DYNAMIC = "dynamic"
    SEMANTIC = "semantic"  # M0 不做
    CONTRACT = "contract"  # M0 不做


@dataclass(frozen=True, slots=True)
class LayerResult:
    """单层验证结果。"""

    layer: VerifyLayer
    passed: bool
    output: str  # stdout/stderr 摘要
    skipped: bool = False  # 没配相应检查时为 True
    # SEMANTIC 层附加：runtime 执行成败 ≠ 审查结论（passed）
    execution_ok: bool | None = None
    runtime_kind: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Verifier 的最终结论。"""

    task_id: str
    passed: bool
    layers: list[LayerResult]
    feedback: str  # 给 Implementor 重试用的反馈

    @property
    def failed_layer(self) -> VerifyLayer | None:
        for layer in self.layers:
            if not layer.passed and not layer.skipped:
                return layer.layer
        return None
