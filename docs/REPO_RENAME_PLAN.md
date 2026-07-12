# 阶段 B：仓库与包名重命名规划

> **状态：B1 已执行（2026-07-12）** — PyPI `lwa-conduit`、CLI `lwa-conduit`、Python 模块 `lwa_conduit`、数据目录 `.lwa-conduit`；`kiro-conduit` 保留 entry point 别名与迁移逻辑。

Conduit 侧要点与 Bridge 一致：方案 A 已用 **LWA / Conduit** 对外话术，技术名 `lwa-conduit` 保持不变。

**完整检查清单、候选命名（B1/B2）、剩余迁移步骤** 见 Bridge 仓库主文档：

👉 [lwa-bridge/docs/REPO_RENAME_PLAN.md](https://github.com/walterwang0x01/lwa-bridge/blob/main/docs/REPO_RENAME_PLAN.md)

## Conduit 特有注意点

| 项 | 说明 |
|----|------|
| Python 包 `lwa_conduit` | ✅ B1 已改（`kiro_conduit` → `lwa_conduit`） |
| CLI `lwa-conduit` | ✅ B1 已改（`kiro-conduit` alias 保留） |
| worktree 分支前缀 `lwa-conduit/` | 可保留，避免污染已有 git 历史 |
| Bridge `/conduit` | 依赖 PATH 上的 `lwa-conduit`；改名时需同步 `LWA_CONDUIT_BIN` 文档 |

## 决策门（与 Bridge 同步）

1. 是否进入 **B1** 全量迁移（PyPI 包名 + 可选 CLI alias）
2. deprecation 窗口（建议 ≥ 90 天）

跟踪 Issue：见本仓库 Labels `phase-b`。
