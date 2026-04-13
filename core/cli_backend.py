"""
Helix CLI Backend
Routes all AI calls through `claude -p` (Claude Code CLI) so they use
the Max subscription natively — no "extra usage" / API key required.

Session continuity via --resume: each (channel, peer) pair gets a persistent
Claude Code session that resumes across messages. New sessions use --session-id
to assign a known UUID. Falls back to a fresh session if resume fails.
"""

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("helix.cli_backend")

def _find_claude_bin() -> str:
    """Locate the claude binary — checks PATH first, then common install locations."""
    found = shutil.which("claude")
    if found:
        return found
    fallbacks = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
    ]
    for path in fallbacks:
        if path.exists():
            return str(path)
    raise RuntimeError(
        "claude binary not found. Install Claude Code: https://docs.anthropic.com/claude-code"
    )

CLAUDE_BIN = _find_claude_bin()
HELIX_DIR = Path(__file__).parent.parent  # consistent cwd — required for --resume to work
DEFAULT_TIMEOUT = 3600  # seconds — long enough for nmap scans, brute force, etc.


def _build_env() -> dict:
    env = {**os.environ}
    env.setdefault("HOME", str(Path.home()))
    local_bin = str(Path.home() / ".local" / "bin")
    path = env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    if local_bin not in path:
        env["PATH"] = f"{local_bin}:{path}"
    return env


async def _invoke(
    model: str,
    system: str,
    user_message: str,
    session_args: list[str],
    timeout: int,
) -> Tuple[str, str]:
    """
    Run claude -p with the given session flags.
    Returns (response_text, session_id_used).
    """
    cmd = [
        CLAUDE_BIN, "-p",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system,
        "--allowedTools", "Bash Edit Read Write Glob Grep WebFetch WebSearch",
    ] + session_args

    logger.debug(f"CLI call: model={model}, session_args={session_args}, msg_len={len(user_message)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(HELIX_DIR),
        env=_build_env(),
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=user_message.encode("utf-8")),
        timeout=timeout,
    )

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")
        out = stdout.decode("utf-8", errors="replace")
        logger.error(f"claude CLI failed (exit {proc.returncode})\nSTDERR: {err[:1000]}\nSTDOUT: {out[:500]}")
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {err[:500] or out[:500]}")

    raw = stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        raise RuntimeError("claude CLI returned empty output")

    data = json.loads(raw)

    if data.get("is_error"):
        raise RuntimeError(f"Claude error: {data.get('result', data)}")

    return data.get("result", ""), data.get("session_id", "")


async def call_claude(
    model: str,
    system: str,
    user_message: str,
    claude_session_id: Optional[str] = None,
    is_new_session: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[str, str]:
    """
    Call claude CLI. Returns (response_text, claude_session_id_used).

    - New session (is_new_session=True or no claude_session_id): --session-id <new_uuid>
    - Resume: --resume <claude_session_id>
    - If resume fails for any reason, falls back to a fresh --session-id
    """
    if is_new_session or not claude_session_id:
        new_id = str(uuid.uuid4())
        logger.info(f"Starting new Claude session: {new_id}")
        return await _invoke(model, system, user_message, ["--session-id", new_id], timeout)

    # Try resume
    try:
        logger.debug(f"Resuming Claude session: {claude_session_id}")
        return await _invoke(model, system, user_message, ["--resume", claude_session_id], timeout)
    except Exception as e:
        logger.warning(f"Resume failed for session {claude_session_id}, falling back to new session: {e}")
        new_id = str(uuid.uuid4())
        return await _invoke(model, system, user_message, ["--session-id", new_id], timeout)
