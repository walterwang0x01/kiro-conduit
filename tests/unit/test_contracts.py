"""单元测试：contracts.py 的签名抽取 + 对比。"""

from __future__ import annotations

from textwrap import dedent

import pytest

from kiro_conduit.contracts import (
    ContractDiff,
    diff_signatures,
    extract_signatures,
)


class TestExtractSignatures:
    def test_top_level_function(self) -> None:
        sigs = extract_signatures("def add(a: int, b: int) -> int: ...\n")
        assert sigs == ["func: add(a: int, b: int) -> int"]

    def test_async_function(self) -> None:
        sigs = extract_signatures("async def fetch(url: str) -> bytes: ...\n")
        assert sigs == ["async-func: fetch(url: str) -> bytes"]

    def test_class_with_methods(self) -> None:
        src = dedent(
            """
            class Calc:
                def add(self, a: int, b: int) -> int: ...
                async def mul(self, a: int, b: int) -> int: ...
            """
        )
        sigs = extract_signatures(src)
        assert sigs == [
            "class: Calc()",
            "method: Calc.add(self, a: int, b: int) -> int",
            "async-method: Calc.mul(self, a: int, b: int) -> int",
        ]

    def test_class_with_bases(self) -> None:
        src = "class Foo(Bar, Baz): pass\n"
        sigs = extract_signatures(src)
        assert sigs == ["class: Foo(Bar, Baz)"]

    def test_imports(self) -> None:
        src = dedent(
            """
            import os
            import json as j
            from pathlib import Path
            from typing import Any as Anything
            """
        )
        sigs = extract_signatures(src)
        assert "import: os" in sigs
        assert "import: json as j" in sigs
        assert "from-import: pathlib.Path" in sigs
        assert "from-import: typing.Any as Anything" in sigs

    def test_keyword_only_arg(self) -> None:
        src = "def f(a: int, *, b: int = 0) -> None: ...\n"
        sigs = extract_signatures(src)
        # default value 应该用 DEFAULT 占位，不放字面量
        assert sigs == ["func: f(a: int, *, b: int=DEFAULT) -> None"]

    def test_positional_only_arg(self) -> None:
        src = "def f(a: int, /, b: int) -> None: ...\n"
        sigs = extract_signatures(src)
        assert sigs == ["func: f(a: int, /, b: int) -> None"]

    def test_varargs_kwargs(self) -> None:
        src = "def f(*args: int, **kwargs: str) -> None: ...\n"
        sigs = extract_signatures(src)
        assert sigs == ["func: f(*args: int, **kwargs: str) -> None"]

    def test_no_annotation(self) -> None:
        src = "def f(a, b): pass\n"
        sigs = extract_signatures(src)
        assert sigs == ["func: f(a, b)"]

    def test_default_uses_placeholder(self) -> None:
        src = "def f(a: int = 1, b: str = 'hi') -> None: ...\n"
        sigs = extract_signatures(src)
        # 默认值不进契约，只要 'DEFAULT' 标记
        assert sigs == ["func: f(a: int=DEFAULT, b: str=DEFAULT) -> None"]

    def test_syntax_error_returns_empty(self) -> None:
        sigs = extract_signatures("def broken(:\n")
        assert sigs == []

    def test_only_top_level(self) -> None:
        """嵌套函数 / 类内嵌套类不抽。"""
        src = dedent(
            """
            def outer():
                def inner(): pass

            class Outer:
                class Inner:
                    pass
                def m(self): pass
            """
        )
        sigs = extract_signatures(src)
        # outer 是顶层 func，Outer 是顶层 class，Outer.m 是它的方法
        # inner 和 Outer.Inner 都不被抽
        assert "func: outer()" in sigs
        assert "class: Outer()" in sigs
        assert "method: Outer.m(self)" in sigs
        assert all("inner" not in s.lower() for s in sigs if s != "func: outer()")


class TestDiffSignatures:
    def test_identical_is_empty(self) -> None:
        same = ["func: f()", "class: C()"]
        d = diff_signatures(same, list(same))
        assert d.is_empty
        assert d.added == ()
        assert d.removed == ()

    def test_added(self) -> None:
        d = diff_signatures(["func: f()"], ["func: f()", "func: g()"])
        assert d.added == ("func: g()",)
        assert d.removed == ()
        assert not d.is_empty

    def test_removed(self) -> None:
        d = diff_signatures(["func: f()", "func: g()"], ["func: f()"])
        assert d.removed == ("func: g()",)
        assert d.added == ()

    def test_changed_signature_shows_as_remove_plus_add(self) -> None:
        old = ["func: f(a: int) -> None"]
        new = ["func: f(a: int, b: int) -> None"]
        d = diff_signatures(old, new)
        assert d.removed == ("func: f(a: int) -> None",)
        assert d.added == ("func: f(a: int, b: int) -> None",)

    def test_to_message_empty(self) -> None:
        d = ContractDiff(added=(), removed=())
        assert "no contract drift" in d.to_message()

    def test_to_message_includes_both_sides(self) -> None:
        d = ContractDiff(
            added=("func: new()",),
            removed=("func: old()",)
        )
        msg = d.to_message()
        assert "REMOVED" in msg
        assert "ADDED" in msg
        assert "func: new()" in msg
        assert "func: old()" in msg


class TestRealWorldScenarios:
    """覆盖 M1.1 真实想防御的几种 consumer 偷改接口的场景。"""

    def test_consumer_adds_param_to_method(self) -> None:
        baseline = dedent(
            """
            class Builder:
                def build(self, x: int) -> str: ...
            """
        )
        consumer = dedent(
            """
            class Builder:
                def build(self, x: int, *, verbose: bool = False) -> str:
                    return str(x)
            """
        )
        d = diff_signatures(extract_signatures(baseline), extract_signatures(consumer))
        assert not d.is_empty
        assert any("build" in s for s in d.added)

    def test_consumer_changes_return_type(self) -> None:
        baseline = "def parse(s: str) -> dict: ...\n"
        consumer = "def parse(s: str) -> list: ...\n"
        d = diff_signatures(extract_signatures(baseline), extract_signatures(consumer))
        assert not d.is_empty

    def test_consumer_only_implements_body_no_drift(self) -> None:
        """consumer 只填实现，没改签名 → 应该 0 漂移。"""
        baseline = dedent(
            """
            def add(a: int, b: int) -> int:
                raise NotImplementedError
            """
        )
        consumer = dedent(
            """
            def add(a: int, b: int) -> int:
                return a + b
            """
        )
        d = diff_signatures(extract_signatures(baseline), extract_signatures(consumer))
        assert d.is_empty


@pytest.mark.parametrize(
    "src,expected",
    [
        ("", []),
        ("# just a comment\n", []),
        ('"""docstring only"""\n', []),
        ("x = 1\n", []),  # 模块级赋值不算契约
    ],
)
def test_extract_edge_cases(src: str, expected: list[str]) -> None:
    assert extract_signatures(src) == expected
