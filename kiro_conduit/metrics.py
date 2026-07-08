"""Runtime/model metrics persistence and aggregation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeMetricRecord:
    task_id: str
    runtime_kind: str
    model: str
    passed: bool
    attempts: int
    files_changed: int


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
    for item in raw.get("records", []):
        records.append(RuntimeMetricRecord(**item))
    return records


def save_metrics(path: Path, records: list[RuntimeMetricRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "records": [asdict(record) for record in records[-500:]]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def summarize_metrics(records: list[RuntimeMetricRecord]) -> list[dict[str, object]]:
    rows: dict[tuple[str, str], dict[str, object]] = {}
    for record in records:
        key = (record.runtime_kind, record.model)
        row = rows.setdefault(
            key,
            {
                "runtime_kind": record.runtime_kind,
                "model": record.model,
                "total": 0,
                "success": 0,
                "failed": 0,
                "avg_files_changed": 0,
            },
        )
        row["total"] = int(row["total"]) + 1
        if record.passed:
            row["success"] = int(row["success"]) + 1
        else:
            row["failed"] = int(row["failed"]) + 1
        row["avg_files_changed"] = int(row["avg_files_changed"]) + record.files_changed
    summary: list[dict[str, object]] = []
    for row in rows.values():
        total = int(row["total"])
        success = int(row["success"])
        avg_files = int(row["avg_files_changed"])
        summary.append(
            {
                **row,
                "success_rate": (success / total) if total else 0.0,
                "avg_files_changed": round(avg_files / total) if total else 0,
            }
        )
    return sorted(summary, key=lambda item: (-int(item["total"]), -float(item["success_rate"])))
