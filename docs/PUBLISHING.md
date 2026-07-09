# Release checklist (PyPI)

## Prerequisites

- PyPI project `kiro-conduit` created (or trusted publishing configured for `walterwang0x01/lwa-conduit`)
- GitHub Actions secret / OIDC trusted publisher linked on PyPI

## Publish 0.1.x

1. Ensure `main` is green (CI: ruff, mypy, pytest).
2. Update `CHANGELOG.md` and `pyproject.toml` version if not already bumped.
3. Create a GitHub Release with tag `v0.1.0` (must match `project.version`).
4. Workflow `.github/workflows/release.yml` builds sdist/wheel and uploads to PyPI.

Manual dry-run locally:

```bash
python -m pip install build
python -m build
python -m twine check dist/*
```

## User install paths

```bash
# Recommended
pipx install kiro-conduit

# Or venv
pip install kiro-conduit

# From source
pip install 'git+https://github.com/walterwang0x01/lwa-conduit.git@v0.1.0'
```

Verify:

```bash
kiro-conduit --help
kiro-conduit report --quota-only
```
