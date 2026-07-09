"""Best-effort native CLI quota probes (kiro-cli / gemini-cli)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

QuotaState = Literal["healthy", "depleted", "unknown", "error"]

_RUNTIME_BINS: dict[str, str] = {
    "kiro-cli-acp": "kiro-cli",
    "cursor-agent-cli": "agent",
    "gemini-cli": "gemini",
}


@dataclass(frozen=True, slots=True)
class NativeQuotaResult:
    state: QuotaState
    detail: str
    remaining_ratio: float | None = None


def _resolve_bin(runtime_kind: str) -> str | None:
    override = {
        "kiro-cli-acp": "KIRO_CONDUIT_KIRO_BIN",
        "cursor-agent-cli": "KIRO_CONDUIT_CURSOR_BIN",
        "gemini-cli": "KIRO_CONDUIT_GEMINI_BIN",
    }.get(runtime_kind)
    if override:
        import os

        value = os.environ.get(override, "").strip()
        if value:
            return value
    default = _RUNTIME_BINS.get(runtime_kind)
    if default and shutil.which(default):
        return default
    return None


def _run_json_probe(bin_path: str, args: list[str]) -> dict[str, object] | None:
    try:
        proc = subprocess.run(
            [bin_path, *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("[quota] native probe failed for %s %s: %s", bin_path, args, exc)
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_usage_payload(data: dict[str, object], *, source: str) -> NativeQuotaResult | None:
    depleted = data.get("depleted")
    if depleted is True:
        return NativeQuotaResult(state="depleted", detail=source, remaining_ratio=0.0)
    remaining = data.get("remaining")
    limit = data.get("limit")
    if isinstance(remaining, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        ratio = max(0.0, min(1.0, float(remaining) / float(limit)))
        state: QuotaState = "depleted" if ratio <= 0 else "healthy"
        return NativeQuotaResult(
            state=state,
            detail=f"{source} remaining={remaining}/{limit}",
            remaining_ratio=ratio,
        )
    ratio_val = data.get("remaining_ratio")
    if isinstance(ratio_val, (int, float)):
        ratio = max(0.0, min(1.0, float(ratio_val)))
        state = "depleted" if ratio <= 0 else "healthy"
        return NativeQuotaResult(state=state, detail=source, remaining_ratio=ratio)
    return None


def probe_native_runtime_kind(runtime_kind: str) -> NativeQuotaResult | None:
    if runtime_kind == "cursor-agent-cli":
        return None
    bin_path = _resolve_bin(runtime_kind)
    if not bin_path:
        return None
    if runtime_kind == "kiro-cli-acp":
        data = _run_json_probe(bin_path, ["usage", "--json"])
        if data:
            return _parse_usage_payload(data, source="kiro-cli usage --json")
    if runtime_kind == "gemini-cli":
        data = _run_json_probe(bin_path, ["quota", "--json"])
        if data:
            return _parse_usage_payload(data, source="gemini quota --json")
    return None
