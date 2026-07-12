"""Conduit 数据目录与 git 分支命名；兼容旧 `kiro-conduit` 标识。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CONDUIT_DIR_NAME = ".lwa-conduit"
LEGACY_CONDUIT_DIR_NAME = ".kiro-conduit"

BRANCH_PREFIX = "lwa-conduit"
LEGACY_BRANCH_PREFIX = "kiro-conduit"

INTEGRATION_BRANCH = f"{BRANCH_PREFIX}/integration"
LEGACY_INTEGRATION_BRANCH = f"{LEGACY_BRANCH_PREFIX}/integration"


def env(name: str, *, legacy: str | None = None) -> str | None:
    """读 `LWA_CONDUIT_*`；未设置时回退旧 `KIRO_CONDUIT_*`。"""
    value = os.environ.get(name)
    if value is not None:
        return value
    if legacy is not None:
        return os.environ.get(legacy)
    legacy_name = name.replace("LWA_CONDUIT_", "KIRO_CONDUIT_", 1)
    if legacy_name != name:
        return os.environ.get(legacy_name)
    return None


def conduit_dir(base_repo: Path) -> Path:
    """返回项目内 conduit 状态目录；若仅有旧目录则自动重命名迁移。"""
    base_repo = base_repo.resolve()
    new_dir = base_repo / CONDUIT_DIR_NAME
    legacy_dir = base_repo / LEGACY_CONDUIT_DIR_NAME
    if new_dir.exists() or not legacy_dir.exists():
        return new_dir
    try:
        legacy_dir.rename(new_dir)
    except OSError:
        shutil.copytree(legacy_dir, new_dir, dirs_exist_ok=True)
    return new_dir


def task_branch(task_id: str) -> str:
    return f"{BRANCH_PREFIX}/{task_id}"


def normalize_branch(branch: str) -> str:
    if branch.startswith(f"{LEGACY_BRANCH_PREFIX}/"):
        return BRANCH_PREFIX + branch[len(LEGACY_BRANCH_PREFIX) :]
    return branch


async def resolve_integration_ref(base_repo: Path, base_branch: str) -> str:
    """优先 `lwa-conduit/integration`；否则回退旧分支名；都没有则用 base_branch。"""
    from lwa_conduit.git_utils import run_git

    for ref in (INTEGRATION_BRANCH, LEGACY_INTEGRATION_BRANCH):
        code, _o, _e = await run_git(
            base_repo,
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"],
        )
        if code == 0:
            return ref
    return base_branch
