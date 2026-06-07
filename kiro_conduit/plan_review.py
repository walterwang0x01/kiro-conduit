"""交互式 plan review：planner 输出后在终端逐 task 展示，让人确认/编辑/删除。

灵感来源：hermes-pets 的"想法确认"流程 + Spec Kit 的 analyze → confirm 步骤。

设计：
- 纯终端交互（rich 渲染 + input 确认），不依赖 Web UI
- 每个 task 展示：id / prompt / depends_on / files_owned / acceptance
- 用户操作：[a]ccept / [s]kip / [e]dit prompt / accept [A]ll
- 返回过滤后的 TaskPlan 列表（只含 accepted 的）
- 可编程调用（传入 confirm_fn 替代 stdin，方便测试）
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kiro_conduit.planner import PlanEvaluation, TaskPlan


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """单个 task 的审查决定。"""

    task_id: str
    accepted: bool
    edited_prompt: str | None = None  # 如果编辑了 prompt，存新值


# 默认的终端确认函数
def _terminal_confirm(prompt: str) -> str:
    """从终端读取用户输入。"""
    return input(prompt).strip().lower()


def review_plan(
    tasks: list[TaskPlan],
    evaluation: PlanEvaluation | None = None,
    console: Console | None = None,
    confirm_fn: Callable[[str], str] | None = None,
) -> list[TaskPlan]:
    """交互式 review plan，返回用户确认后的 task 列表。

    参数：
      tasks: planner 输出的 task 列表
      evaluation: 自评结果（可选，有则展示摘要）
      console: rich Console（可选，默认新建）
      confirm_fn: 替代 input() 的确认函数（测试用）

    返回：
      用户确认的 task 列表（可能被过滤或 prompt 被编辑）
    """
    con = console or Console()
    ask = confirm_fn or _terminal_confirm

    # 展示 plan 总览
    con.print()
    con.print(
        Panel(
            f"[bold]Plan Review[/bold]  共 {len(tasks)} 个 task",
            border_style="cyan",
        )
    )

    # 如果有自评结果，展示摘要
    if evaluation:
        con.print(f"  自评: {evaluation.summary()}")
        if evaluation.must_fix:
            con.print("  [red]⚠ must_fix:[/red]")
            for item in evaluation.must_fix:
                con.print(f"    - {item}")
        con.print()

    # 检查是否一次性全部接受
    choice = ask("[A]ccept all / [r]eview one by one? (A/r): ")
    if choice in ("a", ""):
        con.print("[green]✓ 全部接受[/green]")
        return list(tasks)

    # 逐个 review
    accepted: list[TaskPlan] = []
    for i, task in enumerate(tasks, start=1):
        con.print()
        _render_task(con, task, i, len(tasks))

        while True:
            action = ask("  [a]ccept / [s]kip / [e]dit prompt: ")
            if action in ("a", ""):
                accepted.append(task)
                con.print("  [green]✓ accepted[/green]")
                break
            elif action == "s":
                con.print("  [yellow]⊘ skipped[/yellow]")
                break
            elif action == "e":
                con.print(f"  当前 prompt: {task.prompt[:100]}...")
                new_prompt = ask("  新 prompt (直接回车保持不变): ")
                if new_prompt:
                    task = TaskPlan(
                        id=task.id,
                        prompt=new_prompt,
                        depends_on=task.depends_on,
                        files_owned=task.files_owned,
                        acceptance=task.acceptance,
                    )
                accepted.append(task)
                con.print("  [green]✓ accepted (edited)[/green]")
                break
            else:
                con.print("  [red]无效输入，请输入 a/s/e[/red]")

    con.print()
    con.print(
        f"[bold]Review 完成[/bold]: {len(accepted)}/{len(tasks)} task(s) accepted"
    )
    return accepted


def _render_task(con: Console, task: TaskPlan, index: int, total: int) -> None:
    """渲染单个 task 的详情卡片。"""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    table.add_row("id", task.id)
    table.add_row("prompt", task.prompt[:200] + ("..." if len(task.prompt) > 200 else ""))
    if task.depends_on:
        table.add_row("depends_on", ", ".join(task.depends_on))
    if task.files_owned:
        table.add_row("files_owned", ", ".join(task.files_owned))
    if task.acceptance:
        table.add_row("acceptance", "\n".join(task.acceptance))

    title = f"Task {index}/{total}"
    con.print(Panel(table, title=title, border_style="blue"))
