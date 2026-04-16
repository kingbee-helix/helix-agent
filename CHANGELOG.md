# Changelog

All notable changes to Helix Agent will be documented here.

## [Unreleased]

## [1.1.0] - 2026-04-16

### Added
- **Discord status indicators** — messages now show `thinking...` immediately on receipt, switching to `working on it...` after 4 seconds, then replaced with the final reply. Native Discord "[agent name] is typing..." indicator fixed and running alongside.
- **File attachment support (Discord + Telegram)** — drop any file into Discord or Telegram and the agent receives it. 5MB cap, blacklist for large disk images (`.iso`, `.dmg`, `.vmdk`, etc.). All code and script types including `.sh`, `.exe`, `.py` are fully supported.
- **New `core/file_handler.py`** — centralized file validation, save-to-temp, cleanup, and context injection. Temp files written to `/tmp/helix_uploads/` and cleaned up automatically after each response.
- **Preflight checks in `start.sh`** — on every start, Helix now verifies: venv exists, Python 3.10+, Claude Code CLI installed, all `requirements.txt` packages present and correct version. Clear error messages with exact fix commands on any failure.

### Changed
- Discord slash commands (`/status`, `/model`, `/help`, etc.) skip status indicators and respond instantly — no `thinking...` message for commands that don't hit the agent loop.
- On `NO_REPLY` the `thinking...` placeholder is silently deleted rather than left in the channel.

## [1.0.6] - 2026-04-15

### Fixed
- **Model dropdown alias resolution** — the dropdown was showing the wrong model because the session stores a full model ID (e.g. `claude-haiku-4-5-20251022`) but the dropdown uses aliases (e.g. `haiku`). Now resolves the session model ID against the roster to find the correct alias before syncing the dropdown.
- **Config/session load race condition** — `loadConfig()` and `loadSessions()` were running in parallel, so the model roster wasn't available when alias resolution ran. Config now loads first, then sessions resolves correctly against the populated roster.

## [1.0.5] - 2026-04-15

### Fixed
- **Model dropdown not reflecting current session model** — the dropdown was always initialized from the config default, ignoring what model the session was actually running on. Now reads the session's current model after load and syncs the dropdown to match on every page load.

## [1.0.4] - 2026-04-15

### Fixed
- **`/compact → /model` context loss bug** — `set_model()` was unconditionally overwriting `pending_context` with a fresh transcript-derived block, destroying any compaction summary that `/compact` had just stored. Now checks for existing `pending_context` first and only falls back to `_build_context_block()` when none exists. The recommended workflow (`/compact` then `/model`) now works as documented.

## [1.0.3] - 2026-04-14

### Added
- **`/compact` slash command** — compresses the current session history into a concise summary using the lightest available model (haiku). The summary is stored as pending context and injected on the next turn so the agent picks up exactly where it left off. Works in Discord, Telegram, and the web chat — no terminal required. Overwrites the local transcript with the summary and increments the compaction counter.

## [1.0.2] - 2026-04-14

### Added
- **Context preservation on model switch** — when switching models via `/model` or the web UI dropdown, Helix now captures the last 30 user/assistant exchanges and injects them as context on the first turn of the new session. Previously switching models would silently drop all in-conversation context.

### Changed
- `CONTEXT_EXCHANGES = 30` constant in `session_manager.py` controls how many exchange pairs are carried forward (configurable)

## [1.0.1] - 2026-04-14

### Fixed
- **Slash commands in web UI** — `/help`, `/model`, `/status` and all other slash commands now work in the web chat. Previously they were passed to the agent as plain text instead of being intercepted by the command handler.
- **Model switcher dropdown** — Selecting a model in the web UI chat now actually switches the model for that session. Previously the dropdown sent a per-message override that was ignored when `--resume` was active. The fix persists the model to the session and clears the Claude Code session ID so the next turn starts fresh with the selected model.
- **Disconnected status flash** — Removed the yellow "disconnected" indicator that flashed briefly during normal message/reply turns. The WebSocket connection state is still tracked internally but no longer shown in the UI.

## [1.0.0] - 2026-04-13

### Added
- Initial public release
- Claude Code CLI backend — no API key required, uses your Claude Pro/Max subscription via OAuth
- Discord and Telegram adapters
- Web admin UI with real-time chat, session management, config editor, log viewer, and cron scheduler
- Persistent agent identity via workspace files (SOUL.md, IDENTITY.md, USER.md, MEMORY.md, etc.)
- Memory system — daily logs + long-term MEMORY.md
- Custom slash commands via `commands/` directory
- APScheduler-backed cron system for scheduled tasks and heartbeats
- Prompt caching + `--resume` for token-efficient session continuity
- Encrypted secrets store (Fernet + argon2id)
- Allowlist-based auth for Discord and Telegram
- Rate limiting (20/min, 200/hr per user)
- Prompt injection detection
- Interactive first-run setup wizard
- MIT license
