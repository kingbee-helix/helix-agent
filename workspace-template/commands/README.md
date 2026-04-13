# Custom Commands

Drop `.md` files here to create custom slash commands.

## How It Works

Each file becomes a `/command` in Discord and Telegram.

- `commands/standup.md` → `/standup`
- `commands/deploy.md` → `/deploy`
- `commands/report.md` → `/report`

When the command is triggered, the file's contents are injected as instructions
to the agent, along with any arguments the user passed.

## Example

**commands/standup.md:**
```
Generate a brief standup update based on recent memory and git activity.
Format: What I did yesterday, what I'm doing today, any blockers.
Keep it under 5 bullet points total.
```

Then in Discord: `/standup` → agent generates the update.

## Notes

- Files are loaded at command time, not startup — changes take effect immediately
- User args are appended: `/standup focus on the API work` passes "focus on the API work"
- Keep command files focused — one task per command works best
