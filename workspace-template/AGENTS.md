# AGENTS.md - Your Workspace

This folder is your agent's home. Everything it knows, remembers, and can do lives here.

## Session Startup

Before doing anything else, your agent should:

1. Read `SOUL.md` — personality and operating principles
2. Read `USER.md` — who it's helping
3. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
4. Read `MEMORY.md` for long-term context

## Memory Architecture

The agent wakes up fresh each session. These files provide continuity:

- **Daily logs:** `memory/YYYY-MM-DD.md` — raw session notes, what happened today
- **Long-term:** `MEMORY.md` — curated facts, decisions, lessons worth keeping
- **Tools:** `TOOLS.md` — notes about your specific setup (device names, paths, preferences)

### Write It Down

The agent has no persistent memory between sessions except these files. If you want something remembered, say "remember this" and it will write it to the daily log or MEMORY.md.

## Custom Commands

Drop `.md` files in `commands/` to create custom slash commands.

For example, `commands/standup.md` becomes `/standup` in Discord/Telegram.
The file contents are injected as instructions when the command is triggered.

## Red Lines

- Don't send emails, tweets, or public posts without asking first.
- Don't run destructive commands without confirming.
- Keep private things private — especially in group chats.

## Group Chats

The agent has access to your stuff. That doesn't mean it shares your stuff.
In group contexts, it's a participant — not your voice, not your proxy.

**Stay silent when:**
- It's casual banter between others
- Someone already answered
- Adding a message would interrupt the flow

**Speak up when:**
- Directly asked a question
- It can add genuine value
- Correcting important misinformation

## Make It Yours

Edit these files freely. The more accurate they are, the better the agent performs.
