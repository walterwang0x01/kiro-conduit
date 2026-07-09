"""Runtime/model metrics persistence and aggregation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TypedDict


@dataclass(frozen=True, slots=True)
class RuntimeMetricRecord:
    task_id: str
    runtime_kind: str
    model: str
    passed: bool
    attempts: int
    files_changed: int
    task_bucket: str = "conduit-run"
    # reviewer 专用：execution_ok 表示 runtime 本身是否跑通；
    # verdict_pass 是审查结论，不可与 passed 混用。
    execution_ok: bool | None = None
    verdict_pass: bool | None = None
    duration_ms: int = 0


class _MetricRow(TypedDict):
    runtime_kind: str
    model: str
    total: int
    success: int
    failed: int
    avg_files_changed: int
    avg_attempts: int
    avg_duration_ms: int
    verdict_pass: int
    verdict_total: int


def _new_metric_row(runtime_kind: str, model: str) -> _MetricRow:
    return {
        "runtime_kind": runtime_kind,
        "model": model,
        "total": 0,
        "success": 0,
        "failed": 0,
        "avg_files_changed": 0,
        "avg_attempts": 0,
        "avg_duration_ms": 0,
        "verdict_pass": 0,
        "verdict_total": 0,
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _cost_score(runtime_kind: str, model: str) -> float:
    if runtime_kind == "cursor-agent-cli":
        return 1.0
    if runtime_kind == "gemini-cli":
        return 0.92
    lower = model.lower()
    if "opus" in lower:
        return 0.35
    if "sonnet" in lower:
        return 0.65
    if "haiku" in lower:
        return 0.85
    return 0.5


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _row_score(row: dict[str, object]) -> float:
    success_rate = _as_float(row["success_rate"])
    avg_attempts = _as_float(row["avg_attempts"])
    avg_files = _as_float(row["avg_files_changed"])
    avg_duration_ms = _as_float(row.get("avg_duration_ms", 0))
    runtime_kind = str(row["runtime_kind"])
    model = str(row["model"])
    retry_score = 1 / max(avg_attempts, 1.0)
    change_score = 1 / (1 + avg_files / 8)
    # 真实 duration 优先；没有时退回 retry_score 充当效率代理
    speed_score = (
        1 / (1 + avg_duration_ms / 30_000) if avg_duration_ms > 0 else retry_score
    )
    cost_score = _cost_score(runtime_kind, model)
    return _clamp01(
        success_rate * 0.65
        + speed_score * 0.15
        + cost_score * 0.1
        + change_score * 0.05
        + retry_score * 0.05
    )


def metrics_path(base_repo: Path) -> Path:
    return base_repo / ".kiro-conduit" / "runtime-metrics.json"


def load_metrics(path: Path) -> list[RuntimeMetricRecord]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records: list[RuntimeMetricRecord] = []
    known = {f.name for f in fields(RuntimeMetricRecord)}
    for item in raw.get("records", []):
        payload = dict(item)
        payload.setdefault("task_bucket", "conduit-run")
        # 丢弃未知字段，保证向前兼容
        payload = {k: v for k, v in payload.items() if k in known}
        records.append(RuntimeMetricRecord(**payload))
    return records


def save_metrics(path: Path, records: list[RuntimeMetricRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "records": [asdict(record) for record in records[-500:]]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def summarize_metrics(
    records: list[RuntimeMetricRecord], bucket: str | None = None
) -> list[dict[str, object]]:
    rows: dict[tuple[str, str], _MetricRow] = {}
    for record in records:
        if bucket is not None and record.task_bucket != bucket:
            continue
        key = (record.runtime_kind, record.model)
        row = rows.setdefault(key, _new_metric_row(record.runtime_kind, record.model))
        row["total"] += 1
        # reviewer：用 execution_ok 判定 runtime 成败；其它桶继续用 passed
        ok = record.execution_ok if record.execution_ok is not None else record.passed
        if ok:
            row["success"] += 1
        else:
            row["failed"] += 1
        if record.verdict_pass is not None:
            row["verdict_pass"] += 1 if record.verdict_pass else 0
            row["verdict_total"] += 1
        row["avg_files_changed"] += record.files_changed
        row["avg_attempts"] += record.attempts
        row["avg_duration_ms"] += max(0, record.duration_ms)
    summary: list[dict[str, object]] = []
    for row in rows.values():
        total = row["total"]
        success = row["success"]
        avg_files = row["avg_files_changed"]
        avg_attempts = row["avg_attempts"]
        avg_duration = row["avg_duration_ms"]
        success_rate = (success / total) if total else 0.0
        verdict_total = row["verdict_total"]
        out: dict[str, object] = {
            "runtime_kind": row["runtime_kind"],
            "model": row["model"],
            "total": total,
            "success": success,
            "failed": row["failed"],
            "success_rate": success_rate,
            "avg_files_changed": round(avg_files / total) if total else 0,
            "avg_attempts": round(avg_attempts / total, 2) if total else 0,
            "avg_duration_ms": round(avg_duration / total) if total else 0,
            "score": 0.0,
        }
        if verdict_total:
            out["verdict_pass_rate"] = round(row["verdict_pass"] / verdict_total, 3)
        summary.append(out)
    for summary_row in summary:
        summary_row["score"] = _row_score(summary_row)
    return sorted(
        summary,
        key=lambda item: (-_as_float(item["score"]), -_as_float(item["success_rate"]), -_as_int(item["total"])),
    )


def recommend_strategy(
    records: list[RuntimeMetricRecord], bucket: str | None = None
) -> dict[str, object]:
    rows = summarize_metrics(records, bucket=bucket)
    eligible = [row for row in rows if _as_int(row["total"]) >= 3]
    if not eligible:
        return {"sample_size": 0, "reason": "insufficient-history"}
    best_runtime = eligible[0]
    best_kiro = next(
        (row for row in eligible if row["runtime_kind"] == "kiro-cli-acp"),
        None,
    )
    best_runtime_rate = _as_float(best_runtime["success_rate"])
    best_kiro_rate = _as_float(best_kiro["success_rate"]) if best_kiro else None
    return {
        "sample_size": sum(_as_int(row["total"]) for row in eligible),
        "preferred_runtime_kind": (
            best_runtime["runtime_kind"] if best_runtime_rate >= 0.75 else None
        ),
        "preferred_model": (
            best_kiro["model"] if best_kiro and (best_kiro_rate or 0) >= 0.75 else None
        ),
        "runtime_success_rate": best_runtime_rate,
        "model_success_rate": best_kiro_rate,
        "runtime_score": _as_float(best_runtime["score"]),
        "model_score": _as_float(best_kiro["score"]) if best_kiro else None,
        "reason": "history-multi-objective-score",
    }
