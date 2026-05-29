在 worktree 当前目录下做：

创建文件 `src/calc/__init__.py`，内容**完全等于**：

```python
"""calc package: re-exports add / mul / sub."""

from src.calc.add import add  # noqa: F401
from src.calc.mul import mul  # noqa: F401
from src.calc.sub import sub  # noqa: F401
```

不要做别的事。

注意：写这个文件时 `mul.py` 和 `sub.py` 还不存在——这是**故意**的。这个文件
是接口契约（interface stub），后续 pkg-mul 和 pkg-sub 会各自实现这两个文件。
本任务的 acceptance 只校验本文件能 `python3 -m py_compile`，因为 import 错
误是 ImportError，会在运行期才报。

要求：
- 文件能通过 `python3 -m py_compile src/calc/__init__.py`
- 不要改任何其他文件
