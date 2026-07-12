# Changelog

## 0.1.0 — 2026-07-09

### Added

- Multi-CLI runtimes: `kiro-cli-acp`, `cursor-agent-cli`, `gemini-cli`
- Quota probe + fallback routing (`runtime/quota.py`, env overrides, native CLI JSON probes)
- `lwa-conduit report --quota-only` / `--no-quota`
- M2 large-spec integration test (17 tasks / 8 waves, stub e2e)
- Adaptive routing metrics with `execution_ok` / `verdict_pass` separation for reviewer bucket

### Fixed

- mypy strict + ruff CI for metrics, model router, and quota modules
