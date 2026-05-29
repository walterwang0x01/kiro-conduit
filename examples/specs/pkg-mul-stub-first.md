在当前 worktree 目录下做：

1. 创建 `src/calc/mul.py`：定义函数 `def mul(a: int, b: int) -> int`，返回两数之积
2. 创建 `tests/test_mul.py`：用 pytest 写至少 2 个测试用例

**重要：不要修改 `src/calc/__init__.py`**——它已经被前置的 pkg-stub 任务一次性写好，
里面已经把 `mul` 的 import 加上了。你只需要实现 `mul.py` 让那个 import 能 work。

要求：
- `src/calc/mul.py` 能通过 `python3 -m py_compile`
- `tests/test_mul.py` 能通过 `pytest -q`
- 不要碰 `__init__.py`，不要改任何其他文件
