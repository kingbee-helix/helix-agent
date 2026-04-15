# Changelog

All notable changes to Helix Agent will be documented here.

## [Unreleased]

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
