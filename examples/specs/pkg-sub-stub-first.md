在当前 worktree 目录下做：

1. 创建 `src/calc/sub.py`：定义函数 `def sub(a: int, b: int) -> int`，返回 a - b
2. 创建 `tests/test_sub.py`：用 pytest 写至少 2 个测试用例

**重要：不要修改 `src/calc/__init__.py`**——它已经被前置的 pkg-stub 任务一次性写好，
里面已经把 `sub` 的 import 加上了。你只需要实现 `sub.py` 让那个 import 能 work。

要求：
- `src/calc/sub.py` 能通过 `python3 -m py_compile`
- `tests/test_sub.py` 能通过 `pytest -q`
- 不要碰 `__init__.py`，不要改任何其他文件
