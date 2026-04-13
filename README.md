# Helix

A personal AI agent harness powered by Claude Code. Give your AI assistant a name, a personality, persistent memory, and always-on access through Discord, Telegram, or the built-in web UI.

Helix acts as a frontend for [Claude Code](https://docs.anthropic.com/claude-code) — all AI calls route through Claude's CLI using your existing subscription. No API keys needed.

## What You Get

- **Persistent agent identity** — your agent has a name, personality, and memory that survives across sessions
- **Web admin UI** — chat with your agent, manage sessions, view logs, configure settings from your browser
- **Discord + Telegram** — optional always-on messaging so you can talk to your agent from your phone
- **Memory system** — daily logs + long-term memory files your agent reads and writes automatically
- **Custom slash commands** — drop a `.md` file in the commands folder, it becomes a `/command`
- **Scheduled tasks** — cron jobs that run your agent on a schedule (heartbeat checks, automated workflows)
- **Encrypted secrets** — bot tokens and passwords stored with Fernet encryption, machine-bound keys

## Requirements

- **Python 3.10+**
- **Claude Code CLI** — [install here](https://docs.anthropic.com/claude-code)
- **Anthropic subscription** — Claude Pro or Max (Helix uses your subscription via Claude Code, no API key needed)
- **Linux or macOS** (Windows via WSL should work but is untested)

## Quick Start

```bash
git clone https://github.com/your-username/helix.git
cd helix
python3 main.py setup
```

That's it. The setup wizard handles everything automatically:
- Creates a virtual environment and installs dependencies
- Names your agent and sets your info
- Sets a web admin password
- Optionally connects Discord or Telegram
- Configures Claude Code permissions
- Offers to launch immediately

## Starting and Stopping

```bash
./start.sh    # Start in background (logs to ~/.helix/logs/helix.log)
./stop.sh     # Stop gracefully

# Or run directly:
python3 main.py
```

The venv is managed automatically — you never need to activate it manually.

## Project Structure

```
helix/
  main.py              # Entry point + CLI commands
  setup.py             # First-run setup wizard
  start.sh / stop.sh   # Background launcher scripts
  core/
    agent_loop.py      # Message routing + agent execution
    cli_backend.py     # Claude Code CLI interface
    config.py          # Configuration (Pydantic models)
    context_engine.py  # System prompt builder
    session_manager.py # SQLite session tracking + transcripts
  channels/
    base.py            # Channel adapter base class
    discord_adapter.py # Discord integration
    telegram_adapter.py# Telegram integration
    slash_commands.py  # /command handler
  security/
    audit.py           # Structured audit logging
    auth.py            # Allowlist + rate limiting
    input_validator.py # Prompt injection detection
    secrets.py         # Encrypted credential store
  web/
    app.py             # FastAPI admin UI + REST API + WebSocket chat
    static/index.html  # Admin dashboard
  workspace-template/  # Default agent workspace files (copied on setup)
```

## How It Works

Helix wraps Claude Code's CLI (`claude -p`). When you send a message:

1. Your message arrives via Discord, Telegram, or the web UI
2. Helix loads your agent's identity files (AGENTS.md, SOUL.md, etc.) as the system prompt
3. The message is sent to Claude Code with `--resume` for session continuity
4. Claude Code handles everything — tool execution, file operations, web searches
5. The response comes back through the same channel

Your agent has access to the same tools as Claude Code: Bash, file read/write/edit, web search, web fetch. Permissions are managed through Claude Code's `~/.claude/settings.json`.

## Configuration

Everything lives in `~/.helix/`:

| Path | What |
|------|------|
| `config.json` | Main config (channels, models, scheduling) |
| `secrets.enc` | Encrypted secrets (bot tokens, passwords) |
| `workspace/` | Agent's workspace (identity, memory, tools) |
| `sessions/` | Session database + transcripts |
| `logs/` | Operational + audit logs |

Edit config through the admin UI, or directly:
```bash
python main.py config    # Print current config
python main.py secrets list   # List stored secret keys
python main.py secrets set KEY VALUE
```

## Agent Workspace

Your agent's workspace (`~/.helix/workspace/`) contains the files that define who it is:

| File | Purpose |
|------|---------|
| `AGENTS.md` | Operating instructions — how the workspace works |
| `SOUL.md` | Personality and principles |
| `IDENTITY.md` | Name, emoji, vibe |
| `USER.md` | Info about you |
| `MEMORY.md` | Long-term memory (agent updates this over time) |
| `TOOLS.md` | Notes about your specific setup |
| `HEARTBEAT.md` | What to check on periodic heartbeats |
| `memory/` | Daily logs (`YYYY-MM-DD.md`) |
| `commands/` | Custom slash commands (`.md` files) |

Edit these files anytime. The more accurate they are, the better your agent performs. Your agent can also edit them — that's how it learns and remembers.

## Slash Commands

Available in Discord, Telegram, and the web chat:

| Command | What it does |
|---------|-------------|
| `/help` | List all commands |
| `/new` or `/reset` | Start a fresh session |
| `/model [alias]` | Switch model (haiku / sonnet / opus) |
| `/status` | Session info + current model |
| `/memory` | View today's memory log |
| `/think [level]` | Deep reasoning mode |
| `/do <task>` | Execute a task |
| `/remember <text>` | Save something to memory |
| `/forget <text>` | Remove something from memory |
| `/heartbeat` | Trigger a manual heartbeat check |

## Adding Permissions

Helix inherits permissions from Claude Code. During setup, your home directory is automatically added. To grant access to additional directories:

```bash
claude /permissions
```

Or manually edit `~/.claude/settings.json`.

## Security Notes

- The web UI runs on **localhost only** by default — not exposed to the network
- Bot tokens and passwords are **encrypted at rest** using Fernet with argon2id key derivation
- Encryption keys are **machine-bound** — secrets can't be decrypted on a different machine
- Discord and Telegram use **allowlists** — only your user ID can interact with the agent
- **Rate limiting** is built in (20/min, 200/hr per user)
- **Prompt injection detection** flags suspicious patterns in messages
- If you expose the web UI externally, use an HTTPS reverse proxy

## License

MIT — see [LICENSE](LICENSE).
