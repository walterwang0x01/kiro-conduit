"""paths 模块：目录迁移与 integration 分支解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lwa_conduit.paths import (
    CONDUIT_DIR_NAME,
    LEGACY_CONDUIT_DIR_NAME,
    conduit_dir,
    env,
)


def test_conduit_dir_prefers_new(tmp_path: Path) -> None:
    new_dir = tmp_path / CONDUIT_DIR_NAME
    new_dir.mkdir()
    assert conduit_dir(tmp_path) == new_dir


def test_conduit_dir_migrates_legacy(tmp_path: Path) -> None:
    legacy = tmp_path / LEGACY_CONDUIT_DIR_NAME
    legacy.mkdir()
    (legacy / "run-state.json").write_text("{}", encoding="utf-8")
    resolved = conduit_dir(tmp_path)
    assert resolved == tmp_path / CONDUIT_DIR_NAME
    assert resolved.is_dir()
    assert not legacy.exists()
    assert (resolved / "run-state.json").is_file()


def test_env_reads_legacy_kiro_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LWA_CONDUIT_BIN", raising=False)
    monkeypatch.setenv("KIRO_CONDUIT_BIN", "/opt/legacy")
    assert env("LWA_CONDUIT_BIN") == "/opt/legacy"
