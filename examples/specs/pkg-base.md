在 worktree 当前目录下做下面的事：

1. 创建目录 `src/calc/`，里面：
   - `__init__.py`：空文件即可（pkg-mul 和 pkg-sub 后续会往里加导出）
   - `add.py`：定义函数 `def add(a: int, b: int) -> int`，返回两数之和
2. 创建目录 `tests/`，里面：
   - `test_add.py`：用 pytest 写至少 2 个测试覆盖 add（含 0、负数等边界）

要求：
- `src/calc/add.py` 能通过 `python3 -m py_compile` 编译
- `tests/test_add.py` 能通过 `pytest -q` 执行
- 不要改任何其他文件
