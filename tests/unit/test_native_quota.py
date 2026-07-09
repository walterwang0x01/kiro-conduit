from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from kiro_conduit.runtime.native_quota import probe_native_runtime_kind
from kiro_conduit.runtime.quota import probe_all_runtime_kinds, probe_runtime_kind


def test_probe_all_runtime_kinds_lists_three() -> None:
    statuses = probe_all_runtime_kinds()
    assert len(statuses) == 3
    assert {s.runtime_kind for s in statuses} == {
        "kiro-cli-acp",
        "cursor-agent-cli",
        "gemini-cli",
    }


def test_native_kiro_usage_json(monkeypatch) -> None:
    from kiro_conduit.runtime import quota as quota_mod

    monkeypatch.delenv("KIRO_CONDUIT_QUOTA_OVERRIDES", raising=False)
    quota_mod._CACHE.clear()
    payload = json.dumps({"remaining": 3, "limit": 10})
    proc = MagicMock(returncode=0, stdout=payload, stderr="")
    monkeypatch.setattr("kiro_conduit.runtime.native_quota.shutil.which", lambda _: "/bin/kiro-cli")
    with patch("kiro_conduit.runtime.native_quota.subprocess.run", return_value=proc):
        native = probe_native_runtime_kind("kiro-cli-acp")
        assert native is not None
        assert native.state == "healthy"
        quota_mod._CACHE.clear()
        status = probe_runtime_kind("kiro-cli-acp")
    assert status.state == "healthy"
    assert status.remaining_ratio == 0.3
