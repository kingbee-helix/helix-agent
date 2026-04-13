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
        # Migration: add claude_session_id column if it doesn't exist yet
        try:
            await self._db.execute(MIGRATE_ADD_CLAUDE_SESSION_ID)
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

    async def update_activity(self, session_id: str, token_count: int = 0) -> None:
        async with self._lock:
            await self._db.execute(
                "UPDATE sessions SET last_active=?, token_count=token_count+? WHERE session_id=?",
                (_now_ts(), token_count, session_id),
            )
            await self._db.commit()

    async def set_model(self, session_id: str, model_id: str) -> None:
        async with self._lock:
            await self._db.execute(
                "UPDATE sessions SET model=? WHERE session_id=?",
                (model_id, session_id),
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

    def append_message(self, session_id: str, role: str, content) -> None:
        """Append a message to the JSONL transcript (synchronous, safe from async)."""
        path = _transcript_path(self.agent_id, session_id)
        entry = {
            "ts": _now_ts(),
            "role": role,
            "content": content,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def read_transcript(self, session_id: str) -> list[dict]:
        """Read the full transcript as a list of {role, content} dicts."""
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

