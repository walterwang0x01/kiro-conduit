"""Runtime 配额探测与 fallback（与 Bridge quota.ts 契约对齐）。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Literal, TypedDict

from lwa_conduit.runtime.native_quota import probe_native_runtime_kind

QuotaState = Literal["healthy", "depleted", "unknown", "error"]

ALL_RUNTIME_KINDS: tuple[str, ...] = (
    "kiro-cli-acp",
    "cursor-agent-cli",
    "gemini-cli",
)

_MONTHLY_LIMIT_ENV: dict[str, str] = {
    "kiro-cli-acp": "LWA_CONDUIT_KIRO_MONTHLY_LIMIT",
    "cursor-agent-cli": "LWA_CONDUIT_CURSOR_MONTHLY_LIMIT",
    "gemini-cli": "LWA_CONDUIT_GEMINI_MONTHLY_LIMIT",
}


class _QuotaPayload(TypedDict):
    runtime_kind: str
    state: QuotaState
    detail: str
    remaining_ratio: float | None


_CACHE: dict[str, tuple[float, _QuotaPayload]] = {}
_CACHE_TTL_SEC = 600.0


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    runtime_kind: str
    state: QuotaState
    detail: str
    remaining_ratio: float | None = None


def _quota_payload(status: QuotaStatus) -> _QuotaPayload:
    return {
        "runtime_kind": status.runtime_kind,
        "state": status.state,
        "detail": status.detail,
        "remaining_ratio": status.remaining_ratio,
    }


def _cache_status(cache_key: str, status: QuotaStatus) -> None:
    _CACHE[cache_key] = (time.time() + _CACHE_TTL_SEC, _quota_payload(status))


def _load_overrides() -> dict[str, QuotaState]:
    raw = os.environ.get("LWA_CONDUIT_QUOTA_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, QuotaState] = {}
    for key, value in data.items():
        if value in {"healthy", "depleted", "unknown", "error"}:
            out[str(key)] = value
    return out


def _monthly_limit_status(runtime_kind: str, month_usage: int) -> QuotaStatus | None:
    env_key = _MONTHLY_LIMIT_ENV.get(runtime_kind)
    if not env_key:
        return None
    raw_limit = os.environ.get(env_key, "").strip()
    if not raw_limit:
        return None
    try:
        limit = int(raw_limit)
    except ValueError:
        return None
    if limit <= 0:
        return None
    if month_usage >= limit:
        return QuotaStatus(
            runtime_kind=runtime_kind,
            state="depleted",
            detail=f"monthly usage {month_usage}/{limit}",
            remaining_ratio=0.0,
        )
    return QuotaStatus(
        runtime_kind=runtime_kind,
        state="healthy",
        detail=f"monthly usage {month_usage}/{limit}",
        remaining_ratio=max(0.0, 1 - month_usage / limit),
    )


def fallback_kinds_for_bucket(bucket: str) -> list[str]:
    if bucket in {"planner", "reviewer"}:
        return ["kiro-cli-acp", "gemini-cli", "cursor-agent-cli"]
    return ["cursor-agent-cli", "gemini-cli", "kiro-cli-acp"]


def probe_runtime_kind(runtime_kind: str, *, month_usage: int | None = None) -> QuotaStatus:
    cache_key = f"{runtime_kind}:{month_usage if month_usage is not None else '-'}"
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        payload = cached[1]
        return QuotaStatus(
            runtime_kind=payload["runtime_kind"],
            state=payload["state"],
            detail=payload["detail"],
            remaining_ratio=payload["remaining_ratio"],
        )

    overrides = _load_overrides()
    if runtime_kind in overrides:
        status = QuotaStatus(
            runtime_kind=runtime_kind,
            state=overrides[runtime_kind],
            detail="env-override",
        )
        _cache_status(cache_key, status)
        return status

    if month_usage is not None:
        monthly = _monthly_limit_status(runtime_kind, month_usage)
        if monthly is not None:
            _cache_status(cache_key, monthly)
            return monthly

    native = probe_native_runtime_kind(runtime_kind)
    if native is not None:
        status = QuotaStatus(
            runtime_kind=runtime_kind,
            state=native.state,
            detail=native.detail,
            remaining_ratio=native.remaining_ratio,
        )
        _cache_status(cache_key, status)
        return status

    status = QuotaStatus(runtime_kind=runtime_kind, state="unknown", detail="no probe source")
    _cache_status(cache_key, status)
    return status


def probe_all_runtime_kinds(
    *,
    month_usage_by_kind: dict[str, int] | None = None,
) -> list[QuotaStatus]:
    usage = month_usage_by_kind or {}
    return [
        probe_runtime_kind(kind, month_usage=usage.get(kind))
        for kind in ALL_RUNTIME_KINDS
    ]


def is_quota_blocked(status: QuotaStatus) -> bool:
    return status.state in {"depleted", "error"}


def pick_first_available_kind(
    kinds: list[str],
    *,
    month_usage_by_kind: dict[str, int] | None = None,
) -> str | None:
    usage = month_usage_by_kind or {}
    for kind in kinds:
        status = probe_runtime_kind(kind, month_usage=usage.get(kind))
        if not is_quota_blocked(status):
            return kind
    return None
