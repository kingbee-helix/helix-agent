# HEARTBEAT.md

Heartbeats are periodic proactive checks the agent runs in the background.
Edit this file to tell it what to check and when to reach out.

## What to Check

Add or remove items based on what matters to you:

- **Emails** — Any urgent unread messages?
- **Calendar** — Upcoming events in the next 24-48 hours?
- **Reminders** — Anything you asked the agent to follow up on?

## When to Reach Out

**Proactively notify when:**
- An important message or event needs attention
- Something time-sensitive is coming up (<2 hours)
- You asked for a follow-up and the time has arrived

**Stay quiet when:**
- It's late at night (unless urgent)
- Nothing new since last check
- The last check was less than 30 minutes ago

## State Tracking

The agent tracks last-check timestamps in `memory/heartbeat-state.json`
so it doesn't repeat the same checks unnecessarily.

## Custom Tasks

Add recurring tasks here. The agent will pick them up on each heartbeat:

```
- [ ] Check git status on active projects
- [ ] Review open todos
```

---

_Keep this file small — it's loaded every heartbeat, so token cost matters._
