"""
Helix Session Manager
aiosqlite-backed session index + JSONL transcripts per session.
Session routing: per-channel-peer (channel + sender_id = session key).
"""

import asyncio
import json
import uuid
import zoneinfo
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite

from core.config import get_config

SESSIONS_DIR = Path.home() / ".helix" / "sessions"


def _tz():
    return zoneinfo.ZoneInfo(get_config().timezone)

# ─── Schema ───────────────────────────────────────────────────────────────────

CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    agent_id          TEXT NOT NULL,
    channel           TEXT NOT NULL,
    peer              TEXT NOT NULL,
    created_at        REAL NOT NULL,
    last_active       REAL NOT NULL,
    model             TEXT NOT NULL,
    compacted         INTEGER NOT NULL DEFAULT 0,
    token_count       INTEGER NOT NULL DEFAULT 0,
    claude_session_id TEXT
);
"""

MIGRATE_ADD_CLAUDE_SESSION_ID = """
ALTER TABLE sessions ADD COLUMN claude_session_id TEXT;
"""

MIGRATE_ADD_PENDING_CONTEXT = """
ALTER TABLE sessions ADD COLUMN pending_context TEXT;
"""

MIGRATE_ADD_CONTEXT_WINDOW = """
ALTER TABLE sessions ADD COLUMN context_window INTEGER NOT NULL DEFAULT 0;
"""

MIGRATE_ADD_MAX_OUTPUT_TOKENS = """
ALTER TABLE sessions ADD COLUMN max_output_tokens INTEGER NOT NULL DEFAULT 0;
"""

# Number of user/assistant exchange pairs to carry forward on model switch
CONTEXT_EXCHANGES = 30

CREATE_IDX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_channel_peer
    ON sessions (agent_id, channel, peer);
"""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _db_path(agent_id: str) -> Path:
    p = SESSIONS_DIR / agent_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "sessions.db"


def _transcript_path(agent_id: str, session_id: str) -> Path:
    p = SESSIONS_DIR / agent_id
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{session_id}.jsonl"


def _now_ts() -> float:
    return datetime.now(_tz()).timestamp()


def _reset_due(last_active_ts: float, cfg) -> bool:
    """True if the daily reset has passed since last activity (uses configured timezone)."""
    cfg_session = cfg.session
    tz = _tz()
    last = datetime.fromtimestamp(last_active_ts, tz)
    now = datetime.now(tz)
    # Build today's reset time
    reset_today = now.replace(
        hour=cfg_session.daily_reset_hour,
        minute=0, second=0, microsecond=0,
    )
    # If now is before today's reset, check yesterday's reset
    if now < reset_today:
        reset_today -= timedelta(days=1)
    return last < reset_today


# ─── Session Manager ──────────────────────────────────────────────────────────

class SessionManager:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._db = await aiosqlite.connect(_db_path(self.agent_id))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(CREATE_SESSIONS)
        await self._db.execute(CREATE_IDX)
        # Migrations — add columns if they don't exist yet
        for migration in (MIGRATE_ADD_CLAUDE_SESSION_ID, MIGRATE_ADD_PENDING_CONTEXT,
                          MIGRATE_ADD_CONTEXT_WINDOW, MIGRATE_ADD_MAX_OUTPUT_TOKENS):
            try:
                await self._db.execute(migration)
            except Exception:
                pass  # column already exists
        await self._db.commit()

    async def stop(self) -> None:
        if self._db:
            await self._db.close()

    async def get_or_create(
        self,
        channel: str,
        peer: str,
        model: Optional[str] = None,
    ) -> dict:
        cfg = get_config()
        if model is None:
            model = cfg.models.default_id

        async with self._lock:
            # Try to find existing
            async with self._db.execute(
                "SELECT * FROM sessions WHERE agent_id=? AND channel=? AND peer=?",
                (self.agent_id, channel, peer),
            ) as cur:
                row = await cur.fetchone()

            if row:
                session = dict(row)
                # Check if daily reset is due
                if _reset_due(session["last_active"], cfg):
                    await self._db.execute(
                        "DELETE FROM sessions WHERE session_id=?",
                        (session["session_id"],),
                    )
                    await self._db.commit()
                    # Archive old transcript
                    old_path = _transcript_path(self.agent_id, session["session_id"])
                    if old_path.exists():
                        archive = old_path.with_suffix(".jsonl.archived")
                        old_path.rename(archive)
                    session = None

            else:
                session = None

            if session is None:
                session_id = str(uuid.uuid4())
                now = _now_ts()
                await self._db.execute(
                    """INSERT INTO sessions
                       (session_id, agent_id, channel, peer, created_at, last_active, model, claude_session_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (session_id, self.agent_id, channel, peer, now, now, model),
                )
                await self._db.commit()
                session = {
                    "session_id": session_id,
                    "agent_id": self.agent_id,
                    "channel": channel,
                    "peer": peer,
                    "created_at": now,
                    "last_active": now,
                    "model": model,
                    "compacted": 0,
                    "token_count": 0,
                    "claude_session_id": None,
                }

            return session

    async def update_activity(self, session_id: str, usage: Optional[dict] = None) -> None:
        """Update last_active and accumulate token usage stats from a CLI response."""
        token_delta = 0
        context_window = 0
        max_output_tokens = 0
        if usage:
            token_delta = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            context_window = usage.get("context_window", 0)
            max_output_tokens = usage.get("max_output_tokens", 0)

        async with self._lock:
            if context_window:
                await self._db.execute(
                    """UPDATE sessions
                       SET last_active=?, token_count=token_count+?,
                           context_window=?, max_output_tokens=?
                       WHERE session_id=?""",
                    (_now_ts(), token_delta, context_window, max_output_tokens, session_id),
                )
            else:
                await self._db.execute(
                    "UPDATE sessions SET last_active=?, token_count=token_count+? WHERE session_id=?",
                    (_now_ts(), token_delta, session_id),
                )
            await self._db.commit()

    def _build_context_block(self, session_id: str) -> Optional[str]:
        """Format the last CONTEXT_EXCHANGES user/assistant pairs as a context block."""
        messages = self.read_transcript(session_id)
        if not messages:
            return None
        # Take last N exchange pairs (user + assistant = 2 messages per exchange)
        tail = messages[-(CONTEXT_EXCHANGES * 2):]
        lines = [
            "[Context from previous session — carried forward after model switch]",
            "",
        ]
        for msg in tail:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = msg["content"]
            # Truncate very long messages to keep token cost reasonable
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            lines.append(f"{role}: {content}")
        lines += ["", "[End of previous context. Continue from here with the new model.]"]
        return "\n".join(lines)

    async def set_model(self, session_id: str, model_id: str) -> None:
        """Switch model for a session.

        Saves the last CONTEXT_EXCHANGES exchanges as pending_context so the
        first turn on the new session can pick up where the old one left off.
        Clears claude_session_id so the next turn starts a fresh Claude Code session.

        If pending_context is already set (e.g. from a prior /compact), it is
        preserved as-is rather than overwritten with a transcript-derived block.
        """
        async with self._lock:
            # Check for existing pending_context (e.g. set by /compact)
            async with self._db.execute(
                "SELECT pending_context FROM sessions WHERE session_id=?",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()

            existing = row["pending_context"] if row else None
            context_block = existing if existing else self._build_context_block(session_id)

            await self._db.execute(
                "UPDATE sessions SET model=?, claude_session_id=NULL, pending_context=? WHERE session_id=?",
                (model_id, context_block, session_id),
            )
            await self._db.commit()

    async def pop_pending_context(self, session_id: str) -> Optional[str]:
        """Atomically read and clear pending_context. Returns None if not set."""
        async with self._lock:
            async with self._db.execute(
                "SELECT pending_context FROM sessions WHERE session_id=?",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row or not row["pending_context"]:
                return None
            context = row["pending_context"]
            await self._db.execute(
                "UPDATE sessions SET pending_context=NULL WHERE session_id=?",
                (session_id,),
            )
            await self._db.commit()
            return context

    async def compact_session(self, session_id: str, summary: str) -> None:
        """Store compaction summary as pending_context, clear claude_session_id, mark compacted."""
        context_block = (
            "[Compacted session summary — loaded on session resume]\n\n"
            f"{summary}\n\n"
            "[End of compacted context. Continue the conversation from here.]"
        )
        async with self._lock:
            await self._db.execute(
                """UPDATE sessions
                   SET pending_context=?, claude_session_id=NULL, compacted=compacted+1
                   WHERE session_id=?""",
                (context_block, session_id),
            )
            await self._db.commit()

    async def set_claude_session_id(self, session_id: str, claude_session_id: str) -> None:
        """Persist the Claude Code session ID for --resume on future turns."""
        async with self._lock:
            await self._db.execute(
                "UPDATE sessions SET claude_session_id=? WHERE session_id=?",
                (claude_session_id, session_id),
            )
            await self._db.commit()

    async def reset_session(self, channel: str, peer: str) -> None:
        """Force-reset: delete session row + archive transcript."""
        async with self._lock:
            async with self._db.execute(
                "SELECT session_id FROM sessions WHERE agent_id=? AND channel=? AND peer=?",
                (self.agent_id, channel, peer),
            ) as cur:
                row = await cur.fetchone()
            if row:
                sid = row["session_id"]
                await self._db.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
                await self._db.commit()
                tp = _transcript_path(self.agent_id, sid)
                if tp.exists():
                    tp.rename(tp.with_suffix(".jsonl.archived"))

    async def list_sessions(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM sessions WHERE agent_id=? ORDER BY last_active DESC",
            (self.agent_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Transcript ──────────────────────────────────────────────────────────

    def _append_message_sync(self, session_id: str, role: str, content) -> None:
        """Synchronous implementation of append_message (runs in thread executor)."""
        path = _transcript_path(self.agent_id, session_id)
        entry = {
            "ts": _now_ts(),
            "role": role,
            "content": content,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    async def append_message(self, session_id: str, role: str, content) -> None:
        """Append a message to the JSONL transcript.

        Awaits the executor write so callers get ordering guarantees and
        exceptions propagate correctly instead of being silently swallowed.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._append_message_sync, session_id, role, content)

    def _read_transcript_sync(self, session_id: str) -> list[dict]:
        """Synchronous implementation of read_transcript (runs in thread executor)."""
        path = _transcript_path(self.agent_id, session_id)
        if not path.exists():
            return []
        messages = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                messages.append({"role": entry["role"], "content": entry["content"]})
        return messages

    def read_transcript(self, session_id: str) -> list[dict]:
        """Read the full transcript as a list of {role, content} dicts.

        This method keeps a synchronous signature for compatibility with callers
        that cannot await (e.g. snapshot route, context builder).  For the
        /compact path the heavy read is offloaded via async_read_transcript.
        """
        return self._read_transcript_sync(session_id)

    async def async_read_transcript(self, session_id: str) -> list[dict]:
        """Non-blocking version of read_transcript for use in async coroutines."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._read_transcript_sync, session_id)

    def overwrite_transcript(self, session_id: str, messages: list[dict]) -> None:
        """Replace the transcript with a new set of messages (used after compaction)."""
        path = _transcript_path(self.agent_id, session_id)
        now = _now_ts()
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                entry = {"ts": now, "role": msg["role"], "content": msg["content"]}
                f.write(json.dumps(entry) + "\n")

