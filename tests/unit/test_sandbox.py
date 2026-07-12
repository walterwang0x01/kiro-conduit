"""单元测试：OS 写入沙箱包裹（lwa_conduit.sandbox）。

不真起沙箱，只验证 argv 结构 + 无可用工具时原样返回。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lwa_conduit import sandbox


def test_seatbelt_profile_lists_writable_subpaths(tmp_path: Path) -> None:
    prof = sandbox.seatbelt_profile([tmp_path])
    assert "(deny file-write*)" in prof
    assert f'(subpath "{tmp_path.resolve()}")' in prof


def test_wrap_macos_uses_sandbox_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "macos_available", lambda: True)
    monkeypatch.setattr(sandbox, "linux_available", lambda: False)
    out = sandbox.wrap_command(["kiro-cli", "acp"], [tmp_path])
    assert out[0] == "sandbox-exec"
    assert out[1] == "-p"
    assert out[-2:] == ["kiro-cli", "acp"]


def test_wrap_linux_uses_bwrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "macos_available", lambda: False)
    monkeypatch.setattr(sandbox, "linux_available", lambda: True)
    out = sandbox.wrap_command(["kiro-cli", "acp"], [tmp_path])
    assert out[0] == "bwrap"
    assert "--bind" in out
    assert str(tmp_path.resolve()) in out
    assert out[-2:] == ["kiro-cli", "acp"]


def test_wrap_noop_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "macos_available", lambda: False)
    monkeypatch.setattr(sandbox, "linux_available", lambda: False)
    argv = ["kiro-cli", "acp"]
    assert sandbox.wrap_command(argv, [tmp_path]) == argv  # 原样


def test_wrap_empty_argv() -> None:
    assert sandbox.wrap_command([], [Path(".")]) == []
