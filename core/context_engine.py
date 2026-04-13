"""
Helix Context Engine
Builds the system prompt for each agent turn.
- Loads bootstrap files (AGENTS.md, SOUL.md, etc.) as stable cacheable prefix
- Caps each file at 20k chars, total bootstrap at 150k chars
"""

from pathlib import Path

from core.config import get_config

# Bootstrap files in priority order (loaded in this sequence)
BOOTSTRAP_FILES = [
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "HEARTBEAT.md",
    "MEMORY.md",
    "TOOLS.md",
]

FILE_CHAR_LIMIT = 20_000
BOOTSTRAP_CHAR_LIMIT = 150_000


def _load_bootstrap(workspace: Path) -> str:
    """Load all bootstrap files into a single system prompt string."""
    cfg = get_config()
    sections = []
    total = 0

    for filename in BOOTSTRAP_FILES:
        fp = workspace / filename
        if not fp.exists():
            continue
        content = fp.read_text(encoding="utf-8", errors="replace")
        if len(content) > FILE_CHAR_LIMIT:
            content = content[:FILE_CHAR_LIMIT] + f"\n\n[...{filename} truncated at {FILE_CHAR_LIMIT} chars...]"
        chunk = f"## {filename}\n\n{content}\n"
        if total + len(chunk) > BOOTSTRAP_CHAR_LIMIT:
            break
        sections.append(chunk)
        total += len(chunk)

    agent_name = cfg.agent_id.title() if cfg.agent_id else "your AI assistant"
    header = (
        f"You are {agent_name}, an AI assistant. The following files define who you are, "
        "how you operate, and who you are helping. Read them carefully — they are your identity, "
        "memory, and operating instructions.\n\n"
        "---\n\n"
    )
    return header + "\n".join(sections)


def build_system_prompt() -> str:
    """Return the full system prompt (bootstrap files only)."""
    cfg = get_config()
    workspace = Path(cfg.workspace_path)
    return _load_bootstrap(workspace)
