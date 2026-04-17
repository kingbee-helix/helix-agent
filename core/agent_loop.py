"""
Helix Agent Loop
Routes all AI calls through `claude -p` CLI (Max subscription, no extra usage).
Session continuity: each (channel, peer) pair maps to a persistent Claude Code
session resumed with --resume. New day = new session (daily reset).
NO_REPLY sentinel suppresses responses (heartbeat use).
"""

import asyncio
import logging
import traceback
from typing import AsyncGenerator, Optional

from core.config import get_config
from core.session_manager import SessionManager
from core.context_engine import build_system_prompt
from core.cli_backend import call_claude

logger = logging.getLogger("helix.agent")

NO_REPLY = "NO_REPLY"


class AgentLoop:
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def run(
        self,
        channel: str,
        peer: str,
        user_message: str,
        model_override: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Main agent turn. Yields response text.
        Yields NO_REPLY sentinel if response should be suppressed.
        """
        get_config()
        session = await self.session_manager.get_or_create(channel, peer)
        session_id = session["session_id"]
        model_id = model_override or session["model"]
        claude_session_id = session.get("claude_session_id")
        is_new_session = claude_session_id is None

        async with self._get_lock(session_id):
            try:
                system = build_system_prompt(is_new_session=is_new_session)

                # Inject context from previous session if a model switch just happened
                if is_new_session:
                    pending = await self.session_manager.pop_pending_context(session_id)
                    if pending:
                        user_message = f"{pending}\n\n{user_message}"

                response_text, used_session_id, usage = await call_claude(
                    model=model_id,
                    system=system,
                    user_message=user_message,
                    claude_session_id=claude_session_id,
                    is_new_session=is_new_session,
                )

                # Persist claude_session_id if new or changed (fresh start or resume fallback)
                if used_session_id and used_session_id != claude_session_id:
                    await self.session_manager.set_claude_session_id(session_id, used_session_id)

                # NO_REPLY check — match prefix so "NO_REPLY\n\n(explanation)" is also suppressed
                if response_text.strip().startswith(NO_REPLY):
                    yield NO_REPLY
                    return

                # Audit log
                self.session_manager.append_message(session_id, "user", user_message)
                self.session_manager.append_message(session_id, "assistant", response_text)
                await self.session_manager.update_activity(session_id, usage)

                yield response_text

            except Exception as e:
                logger.error(f"Agent loop error [{channel}:{peer}]: {e}\n{traceback.format_exc()}")
                logger.error(f"Full traceback: {traceback.format_exc()}")
                yield "Something went wrong. Please try again."

    async def _collect(self, channel: str, peer: str, message: str, model: str = None) -> Optional[str]:
        """Run agent and collect full response. Returns None for NO_REPLY/errors."""
        try:
            parts = []
            async for chunk in self.run(channel, peer, message, model_override=model):
                parts.append(chunk)
            full = "".join(parts)
            if not full.strip() or full.strip().startswith(NO_REPLY) or "Something went wrong" in full:
                return None
            return full
        except Exception as e:
            logger.warning(f"Agent error [{channel}:{peer}]: {e}")
            return None

    async def run_heartbeat(self, channel: str, peer: str) -> Optional[str]:
        """Silent heartbeat turn. Returns response or None if NO_REPLY."""
        import zoneinfo
        from datetime import datetime as _dt
        cfg = get_config()

        tz = zoneinfo.ZoneInfo(cfg.timezone)
        hour = _dt.now(tz).hour
        if 8 <= hour < 23:
            heartbeat_prompt = (
                "Heartbeat check (daytime). "
                "ONLY reply if there is something genuinely urgent requiring immediate attention. "
                "If nothing is urgent, reply exactly: NO_REPLY"
            )
        else:
            heartbeat_prompt = (
                "Nightly heartbeat. HEARTBEAT.md is already in your context — follow it strictly. "
                "Only run each nightly task once (check heartbeat-state.json). "
                "If all tasks are already done for today, reply exactly: NO_REPLY"
            )
        return await self._collect(channel, "heartbeat", heartbeat_prompt, cfg.models.heartbeat_id)

    async def run_cron(
        self,
        cron_id: str,
        name: str,
        prompt: str,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """Run a user-defined cron job. Returns response or None if NO_REPLY."""
        cfg = get_config()
        model_id = cfg.models.resolve(model) if model else cfg.models.heartbeat_id
        return await self._collect("cron", cron_id, prompt, model_id)
