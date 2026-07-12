"""OS 级写入沙箱（opt-in，experimental）。

把要执行的命令包一层 OS 沙箱，**只限制文件写入**到指定目录（task 的 worktree
/ scratch），读取与网络放开——kiro-cli 在 host 已登录，断读/网会破坏其认证。

- macOS：`sandbox-exec`(Seatbelt)，生成 deny-write-except-subpath 的 profile。
- Linux：`bwrap`(bubblewrap)，整个 / 只读 bind，可写路径 rw bind。
- 两者都没有：原样返回 argv（不沙箱）；调用方用 available() 决定是否启用 / 告警。

对标 Claude Code(Seatbelt/Bubblewrap)、Codex(Landlock) 的 OS 级按命令沙箱思路。
注意：实际隔离效果取决于宿主，需在目标机验证；默认关闭。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Seatbelt：先 allow 全部，再 deny 所有写，最后只放行指定 subpath（后匹配优先）。
# 额外放行 /dev 与临时目录，否则测试/构建写临时文件会被拒。
_SEATBELT_TEMPLATE = """(version 1)
(allow default)
(deny file-write*)
(allow file-write*
{writable}
    (subpath "/dev")
    (subpath "/private/tmp")
    (subpath "/private/var/folders")
)
"""


def macos_available() -> bool:
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def linux_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def available() -> bool:
    """当前平台是否有可用的 OS 沙箱工具。"""
    return macos_available() or linux_available()


def seatbelt_profile(writable: list[Path]) -> str:
    """生成 Seatbelt profile：只允许写 writable 子路径（+ 临时目录）。"""
    rules = "\n".join(f'    (subpath "{p.resolve()}")' for p in writable)
    return _SEATBELT_TEMPLATE.format(writable=rules)


def wrap_command(argv: list[str], writable: list[Path]) -> list[str]:
    """把 argv 包进 OS 写入沙箱，只允许写 writable。无可用沙箱则原样返回。"""
    if not argv:
        return argv
    if macos_available():
        return ["sandbox-exec", "-p", seatbelt_profile(writable), *argv]
    if linux_available():
        cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev",
               "--proc", "/proc", "--tmpfs", "/tmp"]
        for p in writable:
            rp = str(p.resolve())
            cmd += ["--bind", rp, rp]
        return [*cmd, *argv]
    return argv
