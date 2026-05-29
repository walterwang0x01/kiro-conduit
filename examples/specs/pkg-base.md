在 worktree 当前目录下做：

1. 创建文件 `src/calc/add.py`：定义函数 `def add(a: int, b: int) -> int`，返回两数之和
   （父目录 `src/calc/` 不存在的话 mkdir 即可，但**不要**创建 `src/calc/__init__.py`，
   那个文件归另一个 task 管）
2. 创建文件 `tests/test_add.py`：用 pytest 写至少 2 个测试覆盖 add（含 0、负数等边界）

要求：
- `src/calc/add.py` 能通过 `python3 -m py_compile` 编译
- `tests/test_add.py` 能通过 `pytest -q` 执行
- 不要改任何其他文件，不要碰 `__init__.py`

注：测试文件里 `from src.calc.add import add` 这样的相对导入要能跑——
你可以在 `tests/` 下放一个 `conftest.py` 把仓库根加进 sys.path，或者直接
用 `import sys; sys.path.insert(0, '.')` 放在测试文件顶部。
