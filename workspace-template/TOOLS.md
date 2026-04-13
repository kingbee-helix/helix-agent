# TOOLS.md - Local Notes

This file is for setup-specific details that are unique to your environment.
Think of it as a cheat sheet for the agent — paths, device names, credentials hints,
SSH aliases, anything that would otherwise require asking you every time.

## System Info

- **OS:** [e.g. Ubuntu 24.04 / macOS 14 / Windows 11]
- **Package manager:** [e.g. apt, brew, winget]
- **Shell:** [e.g. bash, zsh, fish]

## Key Paths

- **Projects:** [e.g. ~/projects/]
- **Downloads:** [e.g. ~/Downloads/]
- **Config files:** [e.g. ~/.config/]

## Tools & Services

List tools the agent should know about:

```
- docker — installed, runs rootless
- git — configured with your GitHub account
- node v20 — managed via nvm
```

## Notes

Add anything else the agent should know about your specific setup:

- [Example: "I can't sudo — ask me to run sudo commands manually"]
- [Example: "The dev server runs on port 3000"]
- [Example: "WiFi adapter sometimes shows disconnected cosmetically — connection is fine"]

---

_Keep this accurate. Stale info here causes confusion._
