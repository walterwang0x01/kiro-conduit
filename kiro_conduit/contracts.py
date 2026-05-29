"""接口契约：从 Python 源码抽取签名 + 对比"两个版本的签名"是否一致。

用途（M1.1 stub-first）：
- owner 跑完后，把它写出的接口文件的"签名集合"作为契约
- consumer 跑完后，验证它**没有偷偷修改**这些签名

签名抽取范围（M1.1 第一版只抓最有用的）：
- 模块顶层函数 def / async def
- 模块顶层 class，及其顶层方法 def / async def
- import 语句（import foo / from foo import bar [as baz]）

每个签名都序列化成一个稳定字符串：
  "func: name(arg1, arg2=DEFAULT, *args, **kwargs) -> Return"
  "class: name(BaseA, BaseB)"
  "method: ClassName.method_name(...) -> ..."
  "import: foo"
  "from-import: foo.bar (as baz)"

参数默认值用占位符 DEFAULT，不要把 default value 的字面量放进契约——那只是实现细节。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContractDiff:
    """两个签名集合的差。空 = 一致。"""

    added: tuple[str, ...]    # 新版有，旧版没有
    removed: tuple[str, ...]  # 旧版有，新版没有

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed

    def to_message(self) -> str:
        if self.is_empty:
            return "(no contract drift)"
        parts: list[str] = []
        if self.removed:
            parts.append("REMOVED:\n" + "\n".join(f"  - {s}" for s in self.removed))
        if self.added:
            parts.append("ADDED:\n" + "\n".join(f"  + {s}" for s in self.added))
        return "\n".join(parts)


def extract_signatures(source: str) -> list[str]:
    """从 Python 源码字符串抽取接口签名列表。

    无法解析（语法错误）时返回空列表——上层逻辑应该把"空契约"当作信号处理。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[str] = []
    for node in tree.body:
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                out.append(_format_function(node, prefix=""))
            case ast.ClassDef():
                bases = [_format_expr(b) for b in node.bases]
                out.append(f"class: {node.name}({', '.join(bases)})")
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                        out.append(_format_function(sub, prefix=f"{node.name}."))
            case ast.Import():
                for alias in node.names:
                    if alias.asname:
                        out.append(f"import: {alias.name} as {alias.asname}")
                    else:
                        out.append(f"import: {alias.name}")
            case ast.ImportFrom():
                module = node.module or ""
                level = "." * node.level
                for alias in node.names:
                    target = f"{level}{module}.{alias.name}".lstrip(".")
                    if alias.asname:
                        out.append(f"from-import: {target} as {alias.asname}")
                    else:
                        out.append(f"from-import: {target}")
    return out


def diff_signatures(old: list[str], new: list[str]) -> ContractDiff:
    """比对两组签名，返回差异。"""
    old_set = set(old)
    new_set = set(new)
    return ContractDiff(
        added=tuple(sorted(new_set - old_set)),
        removed=tuple(sorted(old_set - new_set)),
    )


# ---------------------------------------------------------------------------
# 内部：格式化
# ---------------------------------------------------------------------------


def _format_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefix: str,
) -> str:
    kind = "method" if prefix else "func"
    if isinstance(node, ast.AsyncFunctionDef):
        kind = f"async-{kind}"
    args = _format_arguments(node.args)
    returns = ""
    if node.returns is not None:
        returns = f" -> {_format_expr(node.returns)}"
    return f"{kind}: {prefix}{node.name}({args}){returns}"


def _format_arguments(args: ast.arguments) -> str:
    parts: list[str] = []

    # 仅位置参数
    posonly = list(args.posonlyargs)
    regular = list(args.args)

    # default 对齐：args.defaults 对应"posonlyargs + args"末尾对齐
    all_pos = posonly + regular
    n_defaults = len(args.defaults)
    has_default = [False] * (len(all_pos) - n_defaults) + [True] * n_defaults

    for i, a in enumerate(posonly):
        parts.append(_format_arg(a, has_default[i]))
    if posonly:
        parts.append("/")
    for i, a in enumerate(regular):
        parts.append(_format_arg(a, has_default[len(posonly) + i]))

    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg, False))
    elif args.kwonlyargs:
        parts.append("*")

    for kw, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(_format_arg(kw, default is not None))

    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg, False))

    return ", ".join(parts)


def _format_arg(arg: ast.arg, has_default: bool) -> str:
    annotation = ""
    if arg.annotation is not None:
        annotation = ": " + _format_expr(arg.annotation)
    suffix = "=DEFAULT" if has_default else ""
    return f"{arg.arg}{annotation}{suffix}"


def _format_expr(node: ast.expr) -> str:
    """把表达式还原成字符串。注解里通常是名字 / 下标 / 属性 / 字符串。"""
    return ast.unparse(node)
