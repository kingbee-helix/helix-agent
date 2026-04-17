"""
Helix Web Admin UI
FastAPI backend on port 18791. WebSocket hub for real-time chat.
JWT auth. REST endpoints for sessions, memory, config, logs, crons.
"""

import asyncio
import json
import logging
import time
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import hmac
import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.config import get_config, save_config
from security.secrets import get_secret, set_secret
from channels.slash_commands import handle_slash

logger = logging.getLogger("helix.web")

app = FastAPI(title="Helix Admin", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:18791", "http://127.0.0.1:18791"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)
_WS_CONNECTIONS: list[WebSocket] = []

# Global references set by main.py
_session_manager = None
_agent_loop = None
_scheduler = None


def init_web(session_manager, agent_loop, scheduler=None):
    global _session_manager, _agent_loop, _scheduler
    _session_manager = session_manager
    _agent_loop = agent_loop
    _scheduler = scheduler


# ─── Auth ─────────────────────────────────────────────────────────────────────

JWT_SECRET_KEY = None

def _get_jwt_secret() -> str:
    global JWT_SECRET_KEY
    if JWT_SECRET_KEY is None:
        JWT_SECRET_KEY = get_secret("WEB_JWT_SECRET")
        if not JWT_SECRET_KEY:
            import secrets as _sec
            JWT_SECRET_KEY = _sec.token_hex(32)
            set_secret("WEB_JWT_SECRET", JWT_SECRET_KEY)
    return JWT_SECRET_KEY


def _create_token() -> str:
    cfg = get_config()
    payload = {
        "sub": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=cfg.web.jwt_expiry_hours),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def _verify_token(token: str) -> bool:
    try:
        jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
        return True
    except Exception:
        return False


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> None:
    if not credentials or not _verify_token(credentials.credentials):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(body: dict):
    """Login with web password to get JWT."""
    password = get_secret("WEB_PASSWORD")
    if not password:
        raise HTTPException(400, "Web password not set. Run: helix secrets set WEB_PASSWORD <pass>")
    if not hmac.compare_digest(body.get("password", ""), password):
        raise HTTPException(401, "Invalid password")
    return {"token": _create_token(), "expires_in": get_config().web.jwt_expiry_hours * 3600}


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Lightweight health check."""
    cfg = get_config()
    sessions = []
    if _session_manager:
        try:
            sessions = await _session_manager.list_sessions()
        except Exception:
            pass
    return {
        "status": "ok",
        "uptime": int(time.time() - _START_TIME),
        "sessions": len(sessions),
        "channels": [
            c for c, enabled in [
                ("discord", cfg.discord.enabled),
                ("telegram", cfg.telegram.enabled),
            ] if enabled
        ],
    }

_START_TIME = time.time()


# ─── Sessions API ─────────────────────────────────────────────────────────────

@app.get("/api/sessions", dependencies=[Depends(require_auth)])
async def list_sessions():
    if not _session_manager:
        return []
    return await _session_manager.list_sessions()


@app.delete("/api/sessions/{channel}/{peer}", dependencies=[Depends(require_auth)])
async def reset_session(channel: str, peer: str):
    if not _session_manager:
        raise HTTPException(404, "Session manager not running")
    await _session_manager.reset_session(channel, peer)
    return {"status": "reset"}


@app.post("/api/sessions/{channel}/{peer}/model", dependencies=[Depends(require_auth)])
async def set_session_model(channel: str, peer: str, body: dict):
    """Switch the model for a session. Clears claude_session_id so next turn starts fresh."""
    if not _session_manager:
        raise HTTPException(404, "Session manager not running")
    model_alias = body.get("model", "")
    cfg = get_config()
    try:
        model_id = cfg.models.resolve(model_alias)
    except ValueError:
        raise HTTPException(400, f"Unknown model: {model_alias}")
    session = await _session_manager.get_or_create(channel, peer)
    await _session_manager.set_model(session["session_id"], model_id)
    return {"status": "switched", "model": model_id}


@app.get("/api/sessions/{session_id}/transcript", dependencies=[Depends(require_auth)])
async def get_transcript(session_id: str):
    if not _session_manager:
        raise HTTPException(404, "Session manager not running")
    # Validate session_id is a UUID to prevent path traversal
    import re
    if not re.match(r'^[a-f0-9\-]{36}$', session_id):
        raise HTTPException(400, "Invalid session ID format")
    messages = _session_manager.read_transcript(session_id)
    return {"session_id": session_id, "messages": messages}


# ─── Memory API ───────────────────────────────────────────────────────────────

@app.get("/api/memory/today", dependencies=[Depends(require_auth)])
async def get_today_memory():
    cfg = get_config()
    tz = zoneinfo.ZoneInfo(cfg.timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    cfg = get_config()
    path = Path(cfg.workspace_path) / "memory" / f"{today}.md"
    if not path.exists():
        return {"date": today, "content": "", "exists": False}
    return {"date": today, "content": path.read_text(), "exists": True}


@app.get("/api/memory/files", dependencies=[Depends(require_auth)])
async def list_memory_files():
    cfg = get_config()
    memory_dir = Path(cfg.workspace_path) / "memory"
    if not memory_dir.exists():
        return []
    files = sorted(memory_dir.glob("*.md"), reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime} for f in files[:30]]



@app.get("/api/memory/long-term", dependencies=[Depends(require_auth)])
async def get_long_term_memory():
    cfg = get_config()
    path = Path(cfg.workspace_path) / "MEMORY.md"
    if not path.exists():
        return {"content": "", "exists": False}
    return {"content": path.read_text(), "exists": True}


@app.put("/api/memory/long-term", dependencies=[Depends(require_auth)])
async def update_long_term_memory(body: dict):
    cfg = get_config()
    path = Path(cfg.workspace_path) / "MEMORY.md"
    content = body.get("content", "")
    path.write_text(content, encoding="utf-8")
    return {"status": "saved"}


# ─── Config API ───────────────────────────────────────────────────────────────

@app.get("/api/config", dependencies=[Depends(require_auth)])
async def get_cfg():
    cfg = get_config()
    data = cfg.model_dump()
    return data


@app.put("/api/config/discord", dependencies=[Depends(require_auth)])
async def update_discord_config(body: dict):
    cfg = get_config()
    if "enabled" in body:
        cfg.discord.enabled = bool(body["enabled"])
    if "allowed_users" in body:
        cfg.discord.allowed_users = body["allowed_users"]
    if "mention_only" in body:
        cfg.discord.mention_only = bool(body["mention_only"])
    save_config(cfg)
    return {"status": "saved"}


@app.put("/api/config/telegram", dependencies=[Depends(require_auth)])
async def update_telegram_config(body: dict):
    cfg = get_config()
    if "enabled" in body:
        cfg.telegram.enabled = bool(body["enabled"])
    if "allowed_users" in body:
        cfg.telegram.allowed_users = body["allowed_users"]
    save_config(cfg)
    return {"status": "saved"}


@app.post("/api/config/models/add", dependencies=[Depends(require_auth)])
async def add_model(body: dict):
    from core.config import ModelEntry
    cfg = get_config()
    entry = ModelEntry(
        id=body["id"],
        alias=body["alias"],
        tier=body.get("tier", "balanced"),
        description=body.get("description", ""),
    )
    # Remove existing with same alias
    cfg.models.roster = [m for m in cfg.models.roster if m.alias != entry.alias and m.id != entry.id]
    cfg.models.roster.append(entry)
    save_config(cfg)
    return {"status": "added", "model": entry.model_dump()}


# ─── Crons API ────────────────────────────────────────────────────────────────

def _cron_next_run(cron_id: str) -> Optional[str]:
    """Get next scheduled run time for a cron job from APScheduler."""
    if not _scheduler:
        return None
    job = _scheduler.get_job(f"cron_{cron_id}")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


@app.get("/api/crons", dependencies=[Depends(require_auth)])
async def list_crons():
    cfg = get_config()
    return [
        {**c.model_dump(), "next_run": _cron_next_run(c.id)}
        for c in cfg.crons
    ]


@app.post("/api/crons", dependencies=[Depends(require_auth)])
async def create_cron(body: dict):
    from core.config import CronJob
    from apscheduler.triggers.cron import CronTrigger

    name = (body.get("name") or "").strip()
    schedule = (body.get("schedule") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not name or not schedule or not prompt:
        raise HTTPException(400, "name, schedule, and prompt are required")

    # Validate the cron expression before saving
    cfg = get_config()
    try:
        trigger = CronTrigger.from_crontab(schedule, timezone=cfg.timezone)
    except Exception as e:
        raise HTTPException(400, f"Invalid cron expression: {e}")

    cfg = get_config()
    cron = CronJob(
        name=name,
        schedule=schedule,
        prompt=prompt,
        enabled=bool(body.get("enabled", True)),
        model=body.get("model") or None,
    )
    cfg.crons.append(cron)
    save_config(cfg)

    if _scheduler and cron.enabled:
        _scheduler.add_job(
            _execute_cron,
            trigger,
            id=f"cron_{cron.id}",
            args=[cron.id],
        )

    return {**cron.model_dump(), "next_run": _cron_next_run(cron.id)}


@app.delete("/api/crons/{cron_id}", dependencies=[Depends(require_auth)])
async def delete_cron(cron_id: str):
    cfg = get_config()
    if not any(c.id == cron_id for c in cfg.crons):
        raise HTTPException(404, "Cron not found")
    cfg.crons = [c for c in cfg.crons if c.id != cron_id]
    save_config(cfg)
    if _scheduler:
        job = _scheduler.get_job(f"cron_{cron_id}")
        if job:
            job.remove()
    return {"status": "deleted"}


@app.patch("/api/crons/{cron_id}/toggle", dependencies=[Depends(require_auth)])
async def toggle_cron(cron_id: str):
    from apscheduler.triggers.cron import CronTrigger
    cfg = get_config()
    cron = next((c for c in cfg.crons if c.id == cron_id), None)
    if not cron:
        raise HTTPException(404, "Cron not found")

    cron.enabled = not cron.enabled
    save_config(cfg)

    if _scheduler:
        job = _scheduler.get_job(f"cron_{cron_id}")
        if cron.enabled and not job:
            try:
                trigger = CronTrigger.from_crontab(cron.schedule, timezone=get_config().timezone)
                _scheduler.add_job(
                    _execute_cron,
                    trigger,
                    id=f"cron_{cron_id}",
                    args=[cron_id],
                )
            except Exception as e:
                logger.warning(f"Failed to re-schedule cron {cron.name}: {e}")
        elif not cron.enabled and job:
            job.remove()

    return {"status": "ok", "enabled": cron.enabled, "next_run": _cron_next_run(cron_id)}


@app.post("/api/crons/{cron_id}/run", dependencies=[Depends(require_auth)])
async def run_cron_now(cron_id: str):
    cfg = get_config()
    cron = next((c for c in cfg.crons if c.id == cron_id), None)
    if not cron:
        raise HTTPException(404, "Cron not found")
    if not _agent_loop:
        raise HTTPException(503, "Agent loop not running")
    asyncio.create_task(_execute_cron(cron_id))
    return {"status": "triggered"}


async def _execute_cron(cron_id: str):
    """Execute a cron job by ID. Used by APScheduler and the 'run now' endpoint."""
    from core.config import load_config, save_config as _save

    cfg = load_config(force_reload=True)
    tz = zoneinfo.ZoneInfo(cfg.timezone)
    cron = next((c for c in cfg.crons if c.id == cron_id), None)
    if not cron or not cron.enabled:
        return

    logger.info(f"Running cron job: {cron.name} ({cron_id})")
    if not _agent_loop:
        logger.warning("Agent loop not available for cron execution")
        return

    result = await _agent_loop.run_cron(cron.id, cron.name, cron.prompt, cron.model)
    if result:
        logger.info(f"Cron [{cron.name}]: {result[:200]}")

    # Update last_run (re-load to avoid overwriting concurrent writes)
    cfg2 = load_config(force_reload=True)
    cron2 = next((c for c in cfg2.crons if c.id == cron_id), None)
    if cron2:
        cron2.last_run = datetime.now(tz).isoformat()
        _save(cfg2)


# ─── Snapshot API ────────────────────────────────────────────────────────────

@app.post("/api/sessions/{channel}/{peer}/snapshot", dependencies=[Depends(require_auth)])
async def take_snapshot(channel: str, peer: str):
    """Write a detailed memory snapshot of the current session to today's daily log.
    Session is left completely untouched — no compaction, no context loss."""
    if not _session_manager:
        raise HTTPException(404, "Session manager not running")
    if not _agent_loop:
        raise HTTPException(503, "Agent loop not running")

    cfg = get_config()
    tz = zoneinfo.ZoneInfo(cfg.timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    ts = datetime.now(tz).strftime("%H:%M")

    session = await _session_manager.get_or_create(channel, peer)
    session_id = session["session_id"]
    messages = _session_manager.read_transcript(session_id)

    if not messages:
        return {"status": "empty", "message": "No conversation to snapshot."}

    # Build a plain-text transcript for the summarization prompt
    transcript_text = "\n\n".join(
        f"[{m['role'].upper()}]: {m['content']}" for m in messages
    )

    snapshot_prompt = (
        "You are writing a detailed memory entry for a personal AI agent's daily log. "
        "Below is a conversation transcript. Your job is to produce a thorough, structured "
        "summary that captures: key topics discussed, decisions made, tasks completed, "
        "important facts or preferences shared by the user, any follow-up items, and "
        "anything the agent should remember for future conversations. "
        "Write in past tense, third-person style (e.g. 'User asked about...', 'Agent explained...'). "
        "Be detailed — this entry replaces the agent's in-context memory before a compaction. "
        "Do NOT include any preamble like 'Here is the summary'. Just the memory entry.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )

    # Use the compaction model (haiku) to write the snapshot
    from core.cli_backend import call_claude
    compaction_model = cfg.models.compaction_id
    summary, _, _ = await call_claude(
        model=compaction_model,
        system="You are an expert at writing detailed memory entries for AI agent logs.",
        user_message=snapshot_prompt,
        is_new_session=True,
    )

    if not summary:
        raise HTTPException(500, "Snapshot generation returned empty response")

    # Append to today's daily memory file
    memory_dir = Path(cfg.workspace_path) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_path = memory_dir / f"{today}.md"

    entry = f"\n\n---\n\n## 📸 Session Snapshot [{ts}]\n\n{summary.strip()}\n"
    with open(memory_path, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info(f"Snapshot written to {memory_path}")
    return {"status": "ok", "message": f"Snapshot saved to memory/{today}.md", "date": today}


# ─── Logs API ─────────────────────────────────────────────────────────────────

@app.get("/api/logs/audit", dependencies=[Depends(require_auth)])
async def get_audit_logs(lines: int = 100):
    lines = min(max(lines, 1), 1000)  # Cap to prevent memory abuse
    audit_path = Path.home() / ".helix" / "logs" / "audit.log"
    if not audit_path.exists():
        return {"lines": []}
    with open(audit_path, "r") as f:
        all_lines = f.readlines()
    recent = all_lines[-lines:]
    parsed = []
    for line in recent:
        try:
            parsed.append(json.loads(line.strip()))
        except Exception:
            parsed.append({"raw": line.strip()})
    return {"lines": parsed}


@app.get("/api/logs/operational", dependencies=[Depends(require_auth)])
async def get_op_logs(lines: int = 100):
    lines = min(max(lines, 1), 1000)  # Cap to prevent memory abuse
    op_path = Path.home() / ".helix" / "logs" / "helix.log"
    if not op_path.exists():
        return {"lines": []}
    with open(op_path, "r") as f:
        all_lines = f.readlines()
    return {"lines": all_lines[-lines:]}


# ─── WebSocket chat ───────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """Real-time chat via WebSocket."""
    await websocket.accept()
    _WS_CONNECTIONS.append(websocket)

    # Auth via first message
    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if not _verify_token(auth_msg.get("token", "")):
            await websocket.send_json({"error": "Unauthorized"})
            await websocket.close()
            return
    except Exception:
        await websocket.close()
        return

    await websocket.send_json({"status": "connected", "agent": "helix"})

    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "")
            if not message:
                continue

            # Handle slash commands before passing to agent loop
            if message.startswith("/"):
                responses = []
                async def send_fn(text):
                    responses.append(text)
                    await websocket.send_json({"chunk": text})
                handled = await handle_slash(message, "web", "admin", _session_manager, _agent_loop, send_fn)
                if handled:
                    full = "".join(responses)
                    await websocket.send_json({"done": True, "full_response": full})
                    continue

            response_parts = []
            async for chunk in _agent_loop.run("web", "admin", message):
                response_parts.append(chunk)
                await websocket.send_json({"chunk": chunk})

            full = "".join(response_parts)
            await websocket.send_json({"done": True, "full_response": full})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        logger.error(f"WebSocket error: {e}\n{traceback.format_exc()}")
    finally:
        if websocket in _WS_CONNECTIONS:
            _WS_CONNECTIONS.remove(websocket)


# ─── Static files ─────────────────────────────────────────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>Helix Admin UI</h1><p>Static files not found.</p>")
