"""
Helix Slash Command Handler
Intercepts /commands before they reach the agent loop.
Harness-handled commands execute immediately without an LLM call.
Agent-handled commands get injected with special framing.
Custom commands loaded from ~/.helix/workspace/commands/*.md
"""

from pathlib import Path
from typing import Callable, Awaitable

from core.config import get_config


# ─── Harness-handled commands ─────────────────────────────────────────────────

HARNESS_COMMANDS = {
    "/new", "/reset", "/stop", "/model", "/status", "/help",
    "/clear", "/session", "/tasks", "/compact",
    "/memory", "/heartbeat", "/agent",
}

# Agent-handled: injected with special framing
AGENT_COMMANDS = {
    "/think", "/do", "/remember", "/forget",
}


async def handle_slash(
    command_str: str,
    channel: str,
    peer: str,
    session_manager,
    agent_loop,
    send_fn: Callable[[str], Awaitable[None]],
) -> bool:
    """
    Process a slash command. Returns True if handled (suppress normal processing).
    send_fn is an async callable that sends a message back to the user.
    """
    parts = command_str.strip().split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    cfg = get_config()

    # ── /help ──────────────────────────────────────────────────────────────
    if cmd == "/help":
        await send_fn(
            "**Helix Commands**\n"
            "`/new` or `/reset` — Start a new session\n"
            "`/model [alias]` — Switch model (haiku/sonnet/opus)\n"
            "`/compact` — Summarize and compress current session history\n"
            "`/status` — Show session + model info\n"
            "`/memory` — Open today's memory log\n"
            "`/clear` — Clear session history\n"
            "`/think [deep]` — Deep reasoning mode\n"
            "`/do <task>` — Execute a task\n"
            "`/remember <text>` — Save to memory\n"
            "`/forget <text>` — Remove from memory\n"
            "`/session` — Show session details\n"
            "`/heartbeat` — Trigger a heartbeat check\n"
        )
        return True

    # ── /new | /reset | /clear ─────────────────────────────────────────────
    if cmd in ("/new", "/reset", "/clear"):
        await session_manager.reset_session(channel, peer)
        await send_fn("Session reset. Fresh start.")
        return True

    # ── /model ─────────────────────────────────────────────────────────────
    if cmd == "/model":
        if not args:
            session = await session_manager.get_or_create(channel, peer)
            roster = cfg.models.roster
            lines = [f"Current model: `{session['model']}`", "", "Available:"]
            for m in roster:
                lines.append(f"  `{m.alias}` ({m.tier}) — {m.description}")
            await send_fn("\n".join(lines))
        else:
            try:
                model_id = cfg.models.resolve(args.strip())
                session = await session_manager.get_or_create(channel, peer)
                await session_manager.set_model(session["session_id"], model_id)
                await send_fn(f"Model switched to `{args.strip()}` ({model_id})")
            except ValueError:
                await send_fn(f"Unknown model: `{args}`. Try: haiku, sonnet, opus")
        return True

    # ── /compact ───────────────────────────────────────────────────────────
    if cmd == "/compact":
        session = await session_manager.get_or_create(channel, peer)
        sid = session["session_id"]
        messages = session_manager.read_transcript(sid)

        if len(messages) < 4:
            await send_fn("Nothing to compact — session is too short.")
            return True

        await send_fn(f"Compacting {len(messages)} messages... hang tight.")

        # Format transcript for summarization
        transcript_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in messages
        )

        summary_prompt = (
            "Summarize the following conversation into a concise but thorough context block. "
            "Preserve all important details: decisions made, code written or discussed, "
            "tasks completed, current state of any work in progress, and what was being "
            "worked on. Write it as a briefing for someone picking up this work from where "
            "it left off. Be specific — include file names, variable names, commands, "
            "or anything actionable that was discussed.\n\n"
            f"CONVERSATION:\n{transcript_text}"
        )

        from core.cli_backend import call_claude

        # Use the lightest available model for summarization
        try:
            compact_model = cfg.models.resolve("haiku")
        except ValueError:
            compact_model = cfg.models.default_id

        try:
            summary, _, _usage = await call_claude(
                model=compact_model,
                system="You are an expert at summarizing technical conversations clearly and concisely.",
                user_message=summary_prompt,
                is_new_session=True,
            )
        except Exception as e:
            await send_fn(f"Compact failed: {e}")
            return True

        # Persist summary, clear session, mark compacted
        await session_manager.compact_session(sid, summary)

        # Overwrite local transcript with just the summary
        session_manager.overwrite_transcript(sid, [
            {"role": "assistant", "content": f"[Compacted session summary]\n{summary}"}
        ])

        await send_fn(
            f"✅ Compacted — {len(messages)} messages summarized.\n"
            "Your next message will continue with the full context preserved."
        )
        return True

    # ── /status ────────────────────────────────────────────────────────────
    if cmd == "/status":
        session = await session_manager.get_or_create(channel, peer)
        from datetime import datetime
        import zoneinfo
        tz = zoneinfo.ZoneInfo(cfg.timezone)
        last = datetime.fromtimestamp(session["last_active"], tz).strftime(f"%Y-%m-%d %H:%M {cfg.timezone}")
        tokens_used = session.get("token_count", 0)
        ctx_window = session.get("context_window", 0)

        if ctx_window:
            pct = (tokens_used / ctx_window * 100)
            ctx_line = f"Context: `{tokens_used:,} / {ctx_window:,}` ({pct:.1f}%)"
        else:
            ctx_line = f"Tokens (est): `{tokens_used:,}`"

        lines = [
            "**Helix Status**",
            f"Agent: `{session['agent_id']}`",
            f"Session ID: `{session['session_id'][:8]}...`",
            f"Model: `{session['model']}`",
            f"Last active: {last}",
            f"Compactions: `{session['compacted']}`",
            ctx_line,
        ]
        lines.append(f"Channel: {channel} | Peer: {peer}")

        await send_fn("\n".join(lines))
        return True

    # ── /session ───────────────────────────────────────────────────────────
    if cmd == "/session":
        sessions = await session_manager.list_sessions()
        if not sessions:
            await send_fn("No active sessions.")
        else:
            lines = [f"**Active Sessions** ({len(sessions)})"]
            for s in sessions[:10]:
                lines.append(f"  `{s['session_id'][:8]}` {s['channel']}:{s['peer']} [{s['model']}]")
            await send_fn("\n".join(lines))
        return True

    # ── /memory ────────────────────────────────────────────────────────────
    if cmd == "/memory":
        from datetime import datetime
        import zoneinfo
        tz = zoneinfo.ZoneInfo(cfg.timezone)
        today = datetime.now(tz).strftime("%Y-%m-%d")
        memory_file = Path(cfg.workspace_path) / "memory" / f"{today}.md"
        if memory_file.exists():
            content = memory_file.read_text()
            # Send first 1500 chars
            preview = content[:1500]
            if len(content) > 1500:
                preview += f"\n\n[...{len(content) - 1500} more chars in {memory_file}]"
            await send_fn(f"**Memory log ({today}):**\n{preview}")
        else:
            await send_fn(f"No memory log for today ({today}).")
        return True

    # ── /heartbeat ─────────────────────────────────────────────────────────
    if cmd == "/heartbeat":
        await send_fn("Running heartbeat check...")
        result = await agent_loop.run_heartbeat(channel, peer)
        if result:
            await send_fn(result)
        else:
            await send_fn("HEARTBEAT_OK — nothing needs attention.")
        return True

    # ── Check custom commands ──────────────────────────────────────────────
    commands_dir = Path(cfg.workspace_path) / "commands"
    if commands_dir.exists():
        # cmd is like "/mycmd" -> look for "mycmd.md"
        cmd_name = cmd.lstrip("/")
        cmd_file = commands_dir / f"{cmd_name}.md"
        if cmd_file.exists():
            cmd_content = cmd_file.read_text()
            injected = f"[Custom command /{cmd_name} triggered]\n\n{cmd_content}\n\nUser args: {args}"
            # Run through agent loop
            response_parts = []
            async for chunk in agent_loop.run(channel, peer, injected):
                response_parts.append(chunk)
            full = "".join(response_parts)
            if full.strip() and full.strip() != "NO_REPLY":
                await send_fn(full)
            return True

    # ── Agent-handled commands: wrap and pass through ──────────────────────
    if cmd in AGENT_COMMANDS:
        return False  # Let caller route with modified message

    # Unknown slash command
    await send_fn(f"Unknown command: `{cmd}`. Try `/help`")
    return True


def wrap_agent_slash(command_str: str) -> str:
    """Wrap agent-handled slash commands with special framing."""
    parts = command_str.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "/think":
        level = args or "normal"
        return f"[Deep thinking mode: {level}] Please think carefully and thoroughly about the following, then provide your response."
    elif cmd == "/do":
        return f"[Task request] Please complete the following task:\n\n{args}"
    elif cmd == "/remember":
        return f"[Memory request] Please save the following to your memory:\n\n{args}"
    elif cmd == "/forget":
        return f"[Memory request] Please remove the following from your memory if you have it:\n\n{args}"
    return command_str
