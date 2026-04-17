"""
Helix test suite.

Covers:
- Session create, switch, and compact flows
- Slash command routing (/status, /model, /new)
- Snapshot route (regression for Fix 2 — correct kwargs to call_claude)
- File upload sanitization (regression for Fix 3)
- Context engine new vs resumed session behaviour (regression for Fix 4)
"""

import sqlite3
import sys
import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so we can import project modules without real external deps
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs):
    """Return a freshly-created stub module registered in sys.modules."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------------------
# aiosqlite in-memory stub
# The real aiosqlite may not be installed, so we provide a complete fake that
# is backed by an in-process sqlite3 database.
# ---------------------------------------------------------------------------

class _AsyncCursor:
    """Async cursor: awaitable itself AND usable as an async context manager.

    The session_manager code does both:
      - ``await db.execute(sql)``        → direct await, result is the cursor
      - ``async with db.execute(sql)``   → context manager, __aenter__ gives cursor
    We satisfy both by making execute() return a coroutine-like that also
    implements __aenter__/__aexit__.
    """

    def __init__(self, cur):
        self._cur = cur

    # Awaitable support: ``result = await db.execute(sql)``
    def __await__(self):
        return self._identity().__await__()

    async def _identity(self):
        return self

    # Async context manager: ``async with db.execute(sql) as cur``
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def _row_to_dict(self, row):
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return dict(zip(cols, row))

    async def fetchone(self):
        return self._row_to_dict(self._cur.fetchone())

    async def fetchall(self):
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return [dict(zip(cols, r)) for r in self._cur.fetchall()]


class _AsyncConnection:
    """Async-compatible wrapper around a real in-memory sqlite3.Connection."""

    def __init__(self, _path):
        self._conn = sqlite3.connect(":memory:")
        # row_factory assigned by SessionManager — we ignore it, handle dicts ourselves
        self.row_factory = None

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        return _AsyncCursor(cur)

    async def commit(self):
        self._conn.commit()

    async def close(self):
        self._conn.close()


async def _aiosqlite_connect(_path):
    return _AsyncConnection(_path)


if "aiosqlite" not in sys.modules:
    aiosqlite_mod = _stub_module("aiosqlite")
    aiosqlite_mod.connect = _aiosqlite_connect
    aiosqlite_mod.Row = dict  # SessionManager sets row_factory to aiosqlite.Row


# ---------------------------------------------------------------------------
# Security stubs (argon2 / cryptography may not be installed)
# ---------------------------------------------------------------------------

if "argon2" not in sys.modules:
    argon2_mod = _stub_module("argon2")
    argon2_mod.low_level = _stub_module("argon2.low_level")
    argon2_mod.low_level.hash_secret_raw = lambda *a, **kw: b"\x00" * 32
    argon2_mod.low_level.Type = MagicMock()

if "cryptography" not in sys.modules:
    crypto_mod = _stub_module("cryptography")
    fernet_mod = _stub_module("cryptography.fernet")

    class _FakeFernet:
        def __init__(self, key):
            self._key = key

        def encrypt(self, data):
            return data

        def decrypt(self, data):
            return data

    fernet_mod.Fernet = _FakeFernet
    fernet_mod.Fernet.generate_key = staticmethod(lambda: b"k" * 44)

if "security" not in sys.modules:
    sec_mod = _stub_module("security")

if "security.secrets" not in sys.modules:
    sec_secrets = _stub_module("security.secrets")
    sec_secrets.get_secret = MagicMock(return_value=None)
    sec_secrets.set_secret = MagicMock()


# ---------------------------------------------------------------------------
# FastAPI stub (for the snapshot route test — fastapi may not be installed)
# ---------------------------------------------------------------------------

if "fastapi" not in sys.modules:
    fastapi_mod = _stub_module("fastapi")

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            self.status_code = status_code
            self.detail = detail

    def _passthrough_decorator(*args, **kwargs):
        """Decorator that preserves the decorated function unchanged."""
        def _inner(fn):
            return fn
        return _inner

    class _FakeApp:
        """Minimal FastAPI stub: route decorators preserve the function."""
        def add_middleware(self, *a, **kw):
            pass

        def get(self, *a, **kw):
            return _passthrough_decorator(*a, **kw)

        def post(self, *a, **kw):
            return _passthrough_decorator(*a, **kw)

        def put(self, *a, **kw):
            return _passthrough_decorator(*a, **kw)

        def delete(self, *a, **kw):
            return _passthrough_decorator(*a, **kw)

        def patch(self, *a, **kw):
            return _passthrough_decorator(*a, **kw)

        def websocket(self, *a, **kw):
            return _passthrough_decorator(*a, **kw)

        def mount(self, *a, **kw):
            pass

    fastapi_mod.HTTPException = _HTTPException
    fastapi_mod.FastAPI = lambda **kw: _FakeApp()
    fastapi_mod.WebSocket = MagicMock
    fastapi_mod.WebSocketDisconnect = Exception
    fastapi_mod.Depends = lambda x: x
    fastapi_mod.status = MagicMock()

    for _sub in ["fastapi.middleware", "fastapi.middleware.cors",
                  "fastapi.responses", "fastapi.staticfiles", "fastapi.security"]:
        if _sub not in sys.modules:
            _m = _stub_module(_sub)
            _m.CORSMiddleware = MagicMock
            _m.HTMLResponse = MagicMock
            _m.StaticFiles = MagicMock
            _m.HTTPBearer = MagicMock
            _m.HTTPAuthorizationCredentials = MagicMock


# ---------------------------------------------------------------------------
# apscheduler stubs
# ---------------------------------------------------------------------------
for _name in [
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.schedulers.asyncio",
    "apscheduler.triggers",
    "apscheduler.triggers.interval",
    "apscheduler.triggers.cron",
]:
    if _name not in sys.modules:
        _stub_module(_name)

# discord / telegram stubs
for _name in ["discord", "discord.ext", "discord.ext.commands", "telegram", "telegram.ext"]:
    if _name not in sys.modules:
        _stub_module(_name)

# jwt stub
if "jwt" not in sys.modules:
    jwt_mod = _stub_module("jwt")
    jwt_mod.encode = lambda payload, key, algorithm="HS256": "tok"
    jwt_mod.decode = lambda token, key, algorithms=None: {"sub": "admin"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent


def _make_temp_config(tmp_path: Path):
    """Return a HelixConfig pointing at tmp_path as the workspace."""
    from core.config import HelixConfig
    return HelixConfig(workspace_path=str(tmp_path))


# ---------------------------------------------------------------------------
# Fix 3 — File upload sanitization
# ---------------------------------------------------------------------------

class TestFileSanitization:
    """Regression tests for core/file_handler.py path-traversal fix."""

    def setup_method(self):
        import importlib
        import core.file_handler as fh
        importlib.reload(fh)
        self.fh = fh

    def test_basename_stripped(self):
        name = self.fh._sanitize_filename("../../etc/passwd")
        assert "/" not in name
        assert ".." not in name
        assert name == "passwd"

    def test_windows_separator_stripped(self):
        name = self.fh._sanitize_filename("..\\..\\windows\\system32\\evil.exe")
        assert "\\" not in name

    def test_plain_filename_unchanged(self):
        name = self.fh._sanitize_filename("report.pdf")
        assert name == "report.pdf"

    def test_empty_filename_fallback(self):
        name = self.fh._sanitize_filename("")
        assert name == "upload"

    def test_collision_handling(self, tmp_path):
        """When the target file already exists a UUID suffix is appended."""
        import core.file_handler as fh

        existing = tmp_path / "file.txt"
        existing.write_bytes(b"old")

        result = fh._resolve_collision(existing)
        assert result != existing
        assert result.suffix == ".txt"
        assert result.parent == tmp_path

    def test_save_file_sanitizes(self, tmp_path, monkeypatch):
        import core.file_handler as fh
        monkeypatch.setattr(fh, "UPLOAD_DIR", tmp_path)

        saved = fh.save_file("../../evil.txt", b"data")
        assert saved is not None
        # Must be inside the upload dir — no traversal
        assert saved.parent == tmp_path
        assert saved.name == "evil.txt"
        assert saved.read_bytes() == b"data"

    def test_save_file_collision(self, tmp_path, monkeypatch):
        import core.file_handler as fh
        monkeypatch.setattr(fh, "UPLOAD_DIR", tmp_path)

        first = fh.save_file("dup.txt", b"first")
        second = fh.save_file("dup.txt", b"second")
        assert first is not None
        assert second is not None
        assert first != second
        assert second.read_bytes() == b"second"


# ---------------------------------------------------------------------------
# Fix 4 — Context engine new vs resumed session
# ---------------------------------------------------------------------------

class TestContextEngine:
    """Regression tests for core/context_engine.py new-session guard."""

    def test_new_session_returns_content(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        soul_file = tmp_path / "SOUL.md"
        soul_file.write_text("I am Helix.")

        with patch("core.context_engine.get_config", return_value=cfg):
            from core.context_engine import build_system_prompt
            prompt = build_system_prompt(is_new_session=True)

        assert "I am Helix." in prompt
        assert len(prompt) > 0

    def test_resumed_session_returns_empty(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        (tmp_path / "SOUL.md").write_text("I am Helix.")

        with patch("core.context_engine.get_config", return_value=cfg):
            from core.context_engine import build_system_prompt
            prompt = build_system_prompt(is_new_session=False)

        assert prompt == ""

    def test_default_is_new_session(self, tmp_path):
        """Default behaviour (no arg) should load bootstrap — new-session assumed."""
        cfg = _make_temp_config(tmp_path)
        (tmp_path / "AGENTS.md").write_text("Agent instructions.")

        with patch("core.context_engine.get_config", return_value=cfg):
            from core.context_engine import build_system_prompt
            prompt = build_system_prompt()

        assert "Agent instructions." in prompt


# ---------------------------------------------------------------------------
# Fix 1 — Version constant
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_constant_exists(self):
        import main
        assert hasattr(main, "VERSION")

    def test_version_is_string(self):
        import main
        assert isinstance(main.VERSION, str)

    def test_version_not_old_hardcode(self):
        import main
        assert main.VERSION != "1.0.0"

    def test_version_matches_1_0_3(self):
        import main
        assert main.VERSION == "1.0.3"


# ---------------------------------------------------------------------------
# Fix 2 — Snapshot route uses correct call_claude kwargs
# ---------------------------------------------------------------------------

class TestSnapshotRoute:
    """Regression: snapshot must call call_claude with the correct signature."""

    @pytest.mark.anyio
    async def test_snapshot_correct_kwargs(self, tmp_path):
        """call_claude must receive user_message=, model=, system=, is_new_session=."""
        import web.app as app_mod

        session_stub = {
            "session_id": str(uuid.uuid4()),
            "channel": "web",
            "peer": "admin",
        }
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=session_stub)
        session_manager.read_transcript = MagicMock(return_value=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ])

        agent_loop = MagicMock()
        cfg = _make_temp_config(tmp_path)

        captured_kwargs: dict = {}

        async def fake_call_claude(**kwargs):
            captured_kwargs.update(kwargs)
            return ("Summary text", "fake-session-id", {})

        with (
            patch("web.app._session_manager", session_manager),
            patch("web.app._agent_loop", agent_loop),
            patch("web.app.get_config", return_value=cfg),
            patch("core.cli_backend.call_claude", fake_call_claude),
        ):
            await app_mod.take_snapshot("web", "admin")

        # Verify no legacy kwargs were passed
        assert "prompt" not in captured_kwargs, "Legacy kwarg 'prompt=' must not be used"
        assert "session_id" not in captured_kwargs, "Legacy kwarg 'session_id=' must not be used"
        # Verify correct kwargs present
        assert "user_message" in captured_kwargs
        assert "model" in captured_kwargs
        assert "is_new_session" in captured_kwargs
        assert captured_kwargs["is_new_session"] is True


# ---------------------------------------------------------------------------
# Session flows (using in-memory SQLite via our aiosqlite stub)
# ---------------------------------------------------------------------------

class TestSessionFlows:
    """Tests for session create, switch, and compact using in-memory SQLite."""

    def _make_sm(self, tmp_path: Path):
        """Return a SessionManager with path helpers redirected to tmp_path."""
        import core.session_manager as sm_mod
        from core.session_manager import SessionManager

        def _db(agent_id):
            p = tmp_path / agent_id
            p.mkdir(parents=True, exist_ok=True)
            return p / "sessions.db"

        def _tp(agent_id, session_id):
            p = tmp_path / agent_id
            p.mkdir(parents=True, exist_ok=True)
            return p / f"{session_id}.jsonl"

        sm_mod._db_path = _db
        sm_mod._transcript_path = _tp

        return SessionManager(agent_id="test-agent")

    @pytest.mark.anyio
    async def test_create_session(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        sm = self._make_sm(tmp_path)
        with patch("core.session_manager.get_config", return_value=cfg):
            await sm.start()
            session = await sm.get_or_create("discord", "user123")
            await sm.stop()
        assert "session_id" in session
        assert session["channel"] == "discord"
        assert session["peer"] == "user123"

    @pytest.mark.anyio
    async def test_same_channel_peer_returns_same_session(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        sm = self._make_sm(tmp_path)
        with patch("core.session_manager.get_config", return_value=cfg):
            await sm.start()
            s1 = await sm.get_or_create("discord", "user123")
            s2 = await sm.get_or_create("discord", "user123")
            await sm.stop()
        assert s1["session_id"] == s2["session_id"]

    @pytest.mark.anyio
    async def test_reset_session_removes_row(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        sm = self._make_sm(tmp_path)
        with patch("core.session_manager.get_config", return_value=cfg):
            await sm.start()
            await sm.get_or_create("discord", "resetme")
            await sm.reset_session("discord", "resetme")
            sessions = await sm.list_sessions()
            await sm.stop()
        assert not any(s["peer"] == "resetme" for s in sessions)

    @pytest.mark.anyio
    async def test_compact_sets_pending_context(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        sm = self._make_sm(tmp_path)
        with patch("core.session_manager.get_config", return_value=cfg):
            await sm.start()
            session = await sm.get_or_create("discord", "compact_user")
            sid = session["session_id"]
            await sm.compact_session(sid, "Compaction summary here.")
            ctx = await sm.pop_pending_context(sid)
            await sm.stop()
        assert ctx is not None
        assert "Compaction summary here." in ctx

    @pytest.mark.anyio
    async def test_pop_pending_context_clears_it(self, tmp_path):
        cfg = _make_temp_config(tmp_path)
        sm = self._make_sm(tmp_path)
        with patch("core.session_manager.get_config", return_value=cfg):
            await sm.start()
            session = await sm.get_or_create("discord", "ctx_user")
            sid = session["session_id"]
            await sm.compact_session(sid, "Summary.")
            await sm.pop_pending_context(sid)
            ctx2 = await sm.pop_pending_context(sid)
            await sm.stop()
        assert ctx2 is None


# ---------------------------------------------------------------------------
# Slash command routing
# ---------------------------------------------------------------------------

class TestSlashCommands:
    """Tests for channels/slash_commands.py routing logic."""

    def _make_session_manager(self):
        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value={
            "session_id": str(uuid.uuid4()),
            "channel": "discord",
            "peer": "user1",
            "agent_id": "helix",
            "model": "claude-sonnet-4-6",
            "last_active": 1_700_000_000.0,
            "compacted": 0,
            "token_count": 0,
            "context_window": 0,
            "max_output_tokens": 0,
        })
        sm.list_sessions = AsyncMock(return_value=[])
        sm.reset_session = AsyncMock()
        sm.set_model = AsyncMock()
        sm.read_transcript = MagicMock(return_value=[])
        return sm

    def _make_agent_loop(self):
        loop = MagicMock()
        loop.run_heartbeat = AsyncMock(return_value=None)
        return loop

    @pytest.mark.anyio
    async def test_status_handled(self, tmp_path):
        responses = []

        async def send(text):
            responses.append(text)

        cfg = _make_temp_config(tmp_path)
        with patch("channels.slash_commands.get_config", return_value=cfg):
            from channels.slash_commands import handle_slash
            handled = await handle_slash(
                "/status", "discord", "user1",
                self._make_session_manager(), self._make_agent_loop(), send
            )

        assert handled is True
        assert responses, "Expected at least one response message"
        combined = "\n".join(responses)
        assert "Model" in combined or "Session" in combined

    @pytest.mark.anyio
    async def test_new_handled(self, tmp_path):
        responses = []

        async def send(text):
            responses.append(text)

        sm = self._make_session_manager()
        cfg = _make_temp_config(tmp_path)

        with patch("channels.slash_commands.get_config", return_value=cfg):
            from channels.slash_commands import handle_slash
            handled = await handle_slash(
                "/new", "discord", "user1",
                sm, self._make_agent_loop(), send
            )

        assert handled is True
        sm.reset_session.assert_awaited_once_with("discord", "user1")

    @pytest.mark.anyio
    async def test_model_switch(self, tmp_path):
        responses = []

        async def send(text):
            responses.append(text)

        sm = self._make_session_manager()
        cfg = _make_temp_config(tmp_path)

        with patch("channels.slash_commands.get_config", return_value=cfg):
            from channels.slash_commands import handle_slash
            handled = await handle_slash(
                "/model sonnet", "discord", "user1",
                sm, self._make_agent_loop(), send
            )

        assert handled is True
        sm.set_model.assert_awaited_once()

    @pytest.mark.anyio
    async def test_unknown_command_handled(self, tmp_path):
        responses = []

        async def send(text):
            responses.append(text)

        cfg = _make_temp_config(tmp_path)
        with patch("channels.slash_commands.get_config", return_value=cfg):
            from channels.slash_commands import handle_slash
            handled = await handle_slash(
                "/doesnotexist", "discord", "user1",
                self._make_session_manager(), self._make_agent_loop(), send
            )

        assert handled is True
        assert any("Unknown" in r or "unknown" in r for r in responses)
