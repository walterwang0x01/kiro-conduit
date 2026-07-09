# Conduit 对外介绍（LWA 编排层）

> 30 秒版本：**把大 spec 拆成 DAG，多个本地 Agent CLI 在 worktree 里按角色并行干活，最后串行 merge。**

**Conduit（kiro-conduit）** 是 **Lark Local Agent Workbench（LWA）** 的 DAG 编排与角色执行层；飞书入口由 **Bridge（lark-kiro-bridge）** 提供。

## 解决什么问题

一份大 spec（几十个 PR、跨模块/跨仓库），单个 Agent session 串行跑几天仍不收敛；手动开多个 worktree 并行，merge 时冲突和接口漂移又让人崩溃。

Conduit 自动化这条链路：spec → DAG → 并行 implementor → verifier/reviewer → 串行 merge。

## 在 LWA 中的位置

| 场景 | 用谁 |
|------|------|
| 飞书里聊代码、轻量编辑、看 Dashboard | Bridge |
| 大 spec、多 worker、长时无人值守 | Conduit |
| 飞书里一句话触发编排 | Bridge `/conduit` → Conduit |

## 默认角色策略

- **planner** → `kiro-cli-acp`（拆 DAG 要稳）
- **implementor** → `cursor-agent-cli`（并行落地要便宜、快）
- **reviewer** → `kiro-cli-acp`（审查能力优先）

配合 `--adaptive-mode suggest` 积累样本后，再逐步切 `apply-safe`。

## 快速开始

```bash
pipx install kiro-conduit
kiro-conduit run \
  --workspace my-workspace/ \
  --runtime-kind cursor-agent-cli \
  --reviewer-runtime-kind kiro-cli-acp \
  --adaptive-mode suggest
```

## 延伸阅读

| 文档 | 内容 |
|------|------|
| [SYSTEM_OVERVIEW.md](./SYSTEM_OVERVIEW.md) | Conduit 在 LWA 中的角色 |
| [runtime-routing.md](./runtime-routing.md) | 生产调参与 adaptive |
| [USAGE.md](./USAGE.md) | CLI 完整用法 |
| [LWA 全体系 pitch](https://github.com/walterwang0x01/lark-kiro-bridge/blob/main/docs/PITCH.md) | Bridge 仓库的对外介绍 |

## 对外一句话（可直接复制）

> **Conduit** 是 LWA 的 DAG 编排层：大 spec 自动拆任务、多 CLI 按角色并行执行、审查后串行 merge；与 Bridge 飞书入口配合，覆盖从日常对话到长时编排的完整本地 Agent 工作流。
