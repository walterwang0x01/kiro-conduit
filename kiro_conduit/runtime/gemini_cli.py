"""Gemini CLI 适配：gemini -p ... --output-format stream-json。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from kiro_conduit.runtime.types import RuntimeConfig

logger = logging.getLogger(__name__)


def _extract_message_text(obj: dict[str, object]) -> str:
    content = obj.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "".join(parts)
    message = obj.get("message")
    if isinstance(message, dict):
        return _extract_message_text(message)
    text = obj.get("text")
    return str(text) if isinstance(text, str) else ""


async def gemini_prompt_stream(
    runtime: RuntimeConfig,
    *,
    cwd: Path,
    prompt: str,
    resume_id: str | None = None,
) -> AsyncIterator[str]:
    """流式产出 assistant 文本片段。"""
    args: list[str] = []
    if resume_id:
        args.extend(["-r", resume_id])
    args.extend(["-p", prompt, "--output-format", "stream-json", "--skip-trust"])
    if runtime.force:
        args.extend(["--approval-mode", "yolo"])
    if runtime.model:
        args.extend(["-m", runtime.model])

    proc = await asyncio.create_subprocess_exec(
        runtime.bin,
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ = obj.get("type")
        if typ == "init":
            continue
        if typ == "message" and str(obj.get("role", "assistant")) in {"assistant", "model"}:
            text = _extract_message_text(obj)
            if text:
                yield text
    code = await proc.wait()
    if code != 0:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
        logger.warning("gemini cli exit %s: %s", code, stderr[:300])


async def gemini_prompt_text(
    runtime: RuntimeConfig,
    *,
    cwd: Path,
    prompt: str,
    resume_id: str | None = None,
) -> str:
    parts: list[str] = []
    async for chunk in gemini_prompt_stream(runtime, cwd=cwd, prompt=prompt, resume_id=resume_id):
        parts.append(chunk)
    return "".join(parts).strip()
