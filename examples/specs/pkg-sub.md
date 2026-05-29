在当前 worktree 目录下做：

1. 创建 `src/calc/sub.py`：定义函数 `def sub(a: int, b: int) -> int`，返回 a - b
2. 在 `src/calc/__init__.py` 末尾追加一行：`from src.calc.sub import sub  # noqa: F401`
   - 这是共享文件，请只追加，不要修改已有内容
3. 创建 `tests/test_sub.py`：用 pytest 写至少 2 个测试

要求：
- `src/calc/sub.py` 能通过 `python3 -m py_compile`
- `tests/test_sub.py` 能通过 `pytest -q`
