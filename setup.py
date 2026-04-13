"""
Helix Setup Wizard
Interactive first-run setup. Creates ~/.helix/, configures the agent,
sets Claude Code permissions, and optionally pairs a messaging platform.

Usage: python main.py setup
"""

import json
import os
import shutil
import subprocess
import sys
import time
from getpass import getpass
from pathlib import Path

HELIX_ROOT = Path(__file__).parent
HELIX_DIR = Path.home() / ".helix"
CONFIG_PATH = HELIX_DIR / "config.json"
WORKSPACE_PATH = HELIX_DIR / "workspace"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
TEMPLATE_DIR = HELIX_ROOT / "workspace-template"


def _ask(prompt: str, default: str = "") -> str:
    """Prompt with optional default."""
    if default:
        result = input(f"{prompt} (press Enter for default '{default}'): ").strip()
        return result or default
    while True:
        result = input(f"{prompt}: ").strip()
        if result:
            return result
        print("  This field is required.")


def _ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    result = input(f"{prompt} ({hint}): ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def _ask_password(prompt: str) -> str:
    while True:
        pw = getpass(f"{prompt}: ")
        if len(pw) < 4:
            print("  Password must be at least 4 characters.")
            continue
        confirm = getpass("  Confirm password: ")
        if pw != confirm:
            print("  Passwords don't match. Try again.")
            continue
        return pw


def _detect_timezone() -> str:
    """Auto-detect the system timezone."""
    # Try /etc/timezone (Debian/Ubuntu)
    tz_file = Path("/etc/timezone")
    if tz_file.exists():
        tz = tz_file.read_text().strip()
        if tz:
            return tz
    # Try timedatectl (systemd)
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Try TZ env var
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    # Try Python's own detection
    try:
        time.tzname[0]
        # tzname gives abbreviations like "CST" — not usable as IANA names
        # Fall back to reading the symlink
        tz_link = Path("/etc/localtime")
        if tz_link.is_symlink():
            target = str(tz_link.resolve())
            if "/zoneinfo/" in target:
                return target.split("/zoneinfo/")[1]
    except Exception:
        pass
    return "UTC"



def _check_claude_installed() -> bool:
    return shutil.which("claude") is not None


def _setup_claude_permissions():
    """Add home directory to Claude Code's allowed paths."""
    home = str(Path.home())
    settings_dir = CLAUDE_SETTINGS.parent
    settings_dir.mkdir(parents=True, exist_ok=True)

    # Load existing settings or start fresh
    if CLAUDE_SETTINGS.exists():
        settings = json.loads(CLAUDE_SETTINGS.read_text())
    else:
        settings = {}

    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])

    # Required permission entries
    needed = [
        f"Read({home}/**)",
        f"Write({home}/**)",
        f"Edit({home}/**)",
        "Read(/tmp/**)",
        "Write(/tmp/**)",
        "Bash",
    ]

    for entry in needed:
        if entry not in allow:
            allow.append(entry)

    permissions.setdefault("defaultMode", "acceptEdits")
    settings["permissions"] = permissions

    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2))
    print(f"  Claude Code permissions set for {home}/")


def _copy_templates(workspace: Path):
    """Copy workspace templates, skip files that already exist."""
    if not TEMPLATE_DIR.exists():
        print("  Warning: workspace-template/ not found, skipping.")
        return

    workspace.mkdir(parents=True, exist_ok=True)
    for item in TEMPLATE_DIR.iterdir():
        dest = workspace / item.name
        if dest.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _write_identity(workspace: Path, name: str, emoji: str):
    path = workspace / "IDENTITY.md"
    path.write_text(
        f"# IDENTITY.md - Who Am I?\n\n"
        f"- **Name:** {name}\n"
        f"- **Emoji:** {emoji}\n"
        f"- **Vibe:** [Edit this to describe your agent's tone]\n"
        f"- **Avatar:** _(optional)_\n"
    )


def _write_user(workspace: Path, name: str, nickname: str, tz: str):
    path = workspace / "USER.md"
    path.write_text(
        f"# USER.md - About Your Human\n\n"
        f"- **Name:** {name}\n"
        f"- **What to call you:** {nickname}\n"
        f"- **Timezone:** {tz}\n\n"
        f"## Preferences\n\n"
        f"_(The agent will fill this in as it learns how you like to work)_\n\n"
        f"## Context\n\n"
        f"_(Background context about you — added over time through conversation)_\n"
    )


def run_setup():
    print()
    print("=" * 50)
    print("  Helix Setup")
    print("=" * 50)
    print()

    # ── Pre-flight checks ──────────────────────────────────────────────
    if not _check_claude_installed():
        print("Error: Claude Code CLI not found.")
        print("Install it first: https://docs.anthropic.com/claude-code")
        print("Then run setup again.")
        sys.exit(1)

    if CONFIG_PATH.exists():
        if not _ask_yn("Helix is already configured. Re-run setup?", default=False):
            print("Setup cancelled.")
            return

    # ── Step 1: Agent identity ─────────────────────────────────────────
    print("— Step 1: Name your agent\n")
    agent_name = _ask("  Agent name", default="Helix")
    agent_emoji = _ask("  Agent emoji", default="🧬")
    agent_id = agent_name.lower().replace(" ", "-")
    print()

    # ── Step 2: About you ──────────────────────────────────────────────
    print("— Step 2: About you\n")
    user_name = _ask("  Your name")
    nickname = _ask("  What should the agent call you?", default=user_name)
    detected_tz = _detect_timezone()
    user_tz = _ask("  Your timezone", default=detected_tz)
    print()

    # ── Step 3: Web admin password ─────────────────────────────────────
    print("— Step 3: Web admin password\n")
    print("  This protects the Helix admin UI.\n")
    web_password = _ask_password("  Set a password")
    print()

    # ── Step 4: Optional messaging platform ────────────────────────────
    print("— Step 4: Messaging platform (optional)\n")
    print("  Your agent is always available through the Helix web UI.")
    print("  You can also connect Discord or Telegram for mobile access.\n")

    discord_config = None
    telegram_config = None

    if _ask_yn("  Pair with a messaging platform?", default=False):
        print()
        print("  1) Discord")
        print("  2) Telegram")
        choice = ""
        while choice not in ("1", "2"):
            choice = input("  Choose (1 or 2): ").strip()

        if choice == "1":
            print()
            print("  Discord setup:")
            print("  1. Create a bot at https://discord.com/developers/applications")
            print("  2. Enable Message Content Intent + Server Members Intent")
            print("  3. Add the bot to your server")
            print()
            discord_token = _ask("  Discord bot token")
            discord_user_id = _ask("  Your Discord user ID (enable Developer Mode to find it)")
            discord_config = {
                "token": discord_token,
                "user_id": discord_user_id,
            }
        else:
            print()
            print("  Telegram setup:")
            print("  1. Message @BotFather on Telegram → /newbot")
            print("  2. Get your user ID from @userinfobot")
            print()
            telegram_token = _ask("  Telegram bot token")
            telegram_user_id = _ask("  Your Telegram user ID")
            telegram_config = {
                "token": telegram_token,
                "user_id": telegram_user_id,
            }
    print()

    # ── Apply everything ───────────────────────────────────────────────
    print("— Setting up...\n")

    # Create directory structure
    HELIX_DIR.mkdir(parents=True, exist_ok=True)
    (HELIX_DIR / "logs").mkdir(exist_ok=True)
    (HELIX_DIR / "sessions").mkdir(exist_ok=True)

    # Copy workspace templates
    _copy_templates(WORKSPACE_PATH)
    print("  Workspace templates installed")

    # Write personalized identity and user files
    _write_identity(WORKSPACE_PATH, agent_name, agent_emoji)
    _write_user(WORKSPACE_PATH, user_name, nickname, user_tz)
    print("  Agent identity and user profile written")

    # Build config
    config = {
        "agent_id": agent_id,
        "timezone": user_tz,
        "workspace_path": str(WORKSPACE_PATH),
        "discord": {"enabled": discord_config is not None},
        "telegram": {"enabled": telegram_config is not None},
    }

    if discord_config:
        config["discord"]["allowed_users"] = [discord_config["user_id"]]
    if telegram_config:
        try:
            config["telegram"]["allowed_users"] = [int(telegram_config["user_id"])]
        except ValueError:
            config["telegram"]["allowed_users"] = [telegram_config["user_id"]]

    # Write config
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    CONFIG_PATH.chmod(0o600)
    print("  Config written to ~/.helix/config.json")

    # Store secrets
    from security.secrets import set_secret
    set_secret("WEB_PASSWORD", web_password)
    if discord_config:
        set_secret("DISCORD_TOKEN", discord_config["token"])
    if telegram_config:
        set_secret("TELEGRAM_TOKEN", telegram_config["token"])
    print("  Secrets encrypted and stored")

    # Set Claude Code permissions
    _setup_claude_permissions()

    # ── Done ───────────────────────────────────────────────────────────
    print()
    print("=" * 50)
    print("  Setup complete!")
    print("=" * 50)
    print()
    print(f"  Agent:    {agent_name} {agent_emoji}")
    print("  Admin UI: http://127.0.0.1:18791")
    print(f"  Config:   {CONFIG_PATH}")
    print(f"  Workspace: {WORKSPACE_PATH}")
    print()
    print("  You can add more channels anytime from the admin UI")
    print("  or just ask your agent to set them up for you.")
    print()
    print("  To manage permissions for additional directories,")
    print("  use: claude /permissions")
    print()

    if _ask_yn("  Launch Helix now?"):
        print()
        print("  Starting Helix...")
        print()
        # Return to main to start the service
        return True

    print()
    print("  To start later: ./start.sh")
    print("  Or: python main.py")
    print()
    return False
