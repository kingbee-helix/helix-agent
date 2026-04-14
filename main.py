"""
Helix — AI Agent Harness
Main entry point. Wires everything together and starts all services.

Usage:
  python main.py                    # Start Helix
  python main.py setup              # First-time setup wizard
  python main.py secrets set KEY V  # Manage secrets
  python main.py secrets list       # List secret keys
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ─── Logging setup ────────────────────────────────────────────────────────────

LOG_PATH = Path.home() / ".helix" / "logs" / "helix.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH),
    ],
)
logger = logging.getLogger("helix.main")


# ─── CLI sub-commands ─────────────────────────────────────────────────────────

def _cli_secrets():
    from security.secrets import set_secret, get_secret, delete_secret, list_keys
    args = sys.argv[2:]
    if not args:
        print("Usage: main.py secrets [set KEY VALUE | get KEY | delete KEY | list]")
        return
    cmd = args[0]
    if cmd == "set" and len(args) == 3:
        set_secret(args[1], args[2])
        print(f"✓ Set {args[1]}")
    elif cmd == "get" and len(args) == 2:
        val = get_secret(args[1])
        print(val if val is not None else f"Key '{args[1]}' not found")
    elif cmd == "delete" and len(args) == 2:
        ok = delete_secret(args[1])
        print("Deleted" if ok else "Not found")
    elif cmd == "list":
        keys = list_keys()
        print("\n".join(keys) if keys else "(empty)")
    else:
        print("Unknown secrets command")


def _cli_config():
    """Print current config."""
    from core.config import load_config
    import json
    cfg = load_config()
    print(json.dumps(cfg.model_dump(), indent=2))


# ─── Scheduler helpers ────────────────────────────────────────────────────────

async def _run_heartbeat_job(agent_loop, audit):
    """APScheduler job: run the agent's heartbeat check."""
    try:
        logger.debug("Running heartbeat check...")
        result = await agent_loop.run_heartbeat(channel="heartbeat", peer="heartbeat")
        if result:
            logger.info(f"Heartbeat response: {result[:200]}")
    except Exception as e:
        logger.warning(f"Heartbeat error: {e}")


# ─── Main service ─────────────────────────────────────────────────────────────

async def main():
    logger.info("Helix starting up...")

    from core.config import load_config
    from core.session_manager import SessionManager
    from core.agent_loop import AgentLoop
    from security.auth import AuthManager
    from security.audit import AuditLogger
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    cfg = load_config()

    # ── Core services ───────────────────────────────────────────────────────
    audit = AuditLogger()
    session_manager = SessionManager(agent_id=cfg.agent_id)
    await session_manager.start()
    logger.info(f"Session manager started (agent: {cfg.agent_id})")

    agent_loop = AgentLoop(session_manager=session_manager)
    auth = AuthManager(audit_logger=audit)

    logger.info(f"Agent loop initialized (default model: {cfg.models.default_id})")

    # ── Web Admin UI ────────────────────────────────────────────────────────
    from web.app import app as fastapi_app, init_web, _execute_cron
    import uvicorn

    # Scheduler created before init_web so the web API has a reference
    scheduler = AsyncIOScheduler(timezone=cfg.timezone)
    init_web(session_manager, agent_loop, scheduler)

    web_config = uvicorn.Config(
        fastapi_app,
        host=cfg.web.host,
        port=cfg.web.admin_port,
        log_level="warning",
        access_log=False,
    )
    web_server = uvicorn.Server(web_config)

    async def _safe_serve():
        try:
            await web_server.serve()
        except SystemExit:
            logger.error(f"Web UI failed to start — port {cfg.web.admin_port} may be in use")
        except Exception as e:
            logger.error(f"Web UI error: {e}")

    asyncio.create_task(_safe_serve())
    logger.info(f"Web admin UI starting on http://{cfg.web.host}:{cfg.web.admin_port}")

    # ── Channels ────────────────────────────────────────────────────────────
    adapters = []

    if cfg.discord.enabled:
        from channels.discord_adapter import DiscordAdapter
        discord_adapter = DiscordAdapter(
            handler=None,
            auth=auth,
            agent_loop=agent_loop,
            session_manager=session_manager,
            audit_logger=audit,
        )
        await discord_adapter.start()
        adapters.append(discord_adapter)
        logger.info("Discord adapter started")
    else:
        logger.info("Discord disabled (set discord.enabled=true in config to enable)")

    if cfg.telegram.enabled:
        from channels.telegram_adapter import TelegramAdapter
        telegram_adapter = TelegramAdapter(
            handler=None,
            auth=auth,
            agent_loop=agent_loop,
            session_manager=session_manager,
            audit_logger=audit,
        )
        await telegram_adapter.start()
        adapters.append(telegram_adapter)
        logger.info("Telegram adapter started")
    else:
        logger.info("Telegram disabled (set telegram.enabled=true in config to enable)")

    # ── Scheduler: heartbeat + user cron jobs ───────────────────────────────
    first_hb = datetime.now() + timedelta(minutes=5)
    scheduler.add_job(
        _run_heartbeat_job,
        IntervalTrigger(minutes=cfg.heartbeat_interval_minutes),
        id="heartbeat",
        next_run_time=first_hb,
        args=[agent_loop, audit],
    )

    for cron in cfg.crons:
        if cron.enabled:
            try:
                scheduler.add_job(
                    _execute_cron,
                    CronTrigger.from_crontab(cron.schedule, timezone=cfg.timezone),
                    id=f"cron_{cron.id}",
                    args=[cron.id],
                )
                logger.info(f"Scheduled cron: {cron.name} ({cron.schedule})")
            except Exception as e:
                logger.warning(f"Failed to schedule cron '{cron.name}': {e}")

    scheduler.start()
    logger.info(f"Scheduler started ({len(cfg.crons)} user cron(s) + heartbeat)")

    logger.info("Helix fully started.")
    logger.info(f"  Admin UI: http://{cfg.web.host}:{cfg.web.admin_port}")

    # ── Wait for shutdown ────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.info(f"Received signal {sig}, shutting down...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    await stop_event.wait()

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    web_server.should_exit = True
    for adapter in adapters:
        try:
            await adapter.stop()
        except Exception as e:
            logger.warning(f"Error stopping adapter: {e}")
    try:
        await session_manager.stop()
    except Exception:
        pass
    await asyncio.sleep(0.5)
    logger.info("Helix stopped.")


def _ensure_venv():
    """Bootstrap .venv if needed. Uses os.execv to re-exec inside the venv."""
    venv_dir = Path(__file__).parent / ".venv"
    venv_python = venv_dir / "bin" / "python"
    requirements = Path(__file__).parent / "requirements.txt"

    if venv_python.exists():
        if sys.prefix == str(venv_dir):
            return  # Already inside the venv
        print("  Activating virtual environment...")
        import os
        os.execv(str(venv_python), [str(venv_python), *sys.argv])

    # Create venv
    import subprocess
    import os
    print("  Creating virtual environment...")
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Error creating venv: {result.stderr}")
        sys.exit(1)

    # Install requirements
    pip = venv_dir / "bin" / "pip"
    if requirements.exists():
        print("  Installing dependencies (this may take a minute)...")
        result = subprocess.run(
            [str(pip), "install", "-q", "-r", str(requirements)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  Error installing dependencies: {result.stderr[:500]}")
            sys.exit(1)
        print("  Dependencies installed.")

    # Re-exec inside the venv so all imports work
    os.execv(str(venv_python), [str(venv_python), *sys.argv])


if __name__ == "__main__":
    # Bootstrap venv before any project imports
    _ensure_venv()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "setup":
            from setup import run_setup
            run_setup()
            sys.exit(0)
        elif cmd == "secrets":
            _cli_secrets()
            sys.exit(0)
        elif cmd == "config":
            _cli_config()
            sys.exit(0)
        elif cmd == "version":
            print("Helix 1.0.0")
            sys.exit(0)
        elif cmd == "help":
            print(__doc__)
            sys.exit(0)

    asyncio.run(main())
