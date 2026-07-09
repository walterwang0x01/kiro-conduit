# 阶段 B：仓库与包名重命名规划（未执行）

> **状态：规划 only，尚未执行任何改名。**

Conduit 侧要点与 Bridge 一致：方案 A 已用 **LWA / Conduit** 对外话术，技术名 `kiro-conduit` 保持不变。

**完整检查清单、候选命名（B1/B2/B3）、迁移步骤** 见 Bridge 仓库主文档：

👉 [lark-kiro-bridge/docs/REPO_RENAME_PLAN.md](https://github.com/walterwang0x01/lark-kiro-bridge/blob/main/docs/REPO_RENAME_PLAN.md)

## Conduit 特有注意点

| 项 | 说明 |
|----|------|
| Python 包 `kiro_conduit` | import 路径改动面最大，建议 CLI/ PyPI 名先变，模块名最后 |
| CLI `kiro-conduit` | 用户脚本、`--help` 示例多；宜保留 alias |
| worktree 分支前缀 `kiro-conduit/` | 可保留，避免污染已有 git 历史 |
| Bridge `/conduit` | 依赖 PATH 上的 `kiro-conduit`；改名时需同步 `KIRO_CONDUIT_BIN` 文档 |

## 决策门（与 Bridge 同步）

1. 选定 B1 / B2 / B3
2. 是否接受 PyPI 包名 breaking change
3. deprecation 窗口（建议 ≥ 90 天）

确认后在两仓库各开 tracking Issue，按主文档清单执行。
