"""
Helix Auth + Rate Limiter
AllowList enforcement per channel. Token bucket rate limiting per sender.
All rejections logged to audit.
"""

import time
from collections import defaultdict, deque
from typing import Optional

from core.config import get_config


class RateLimiter:
    """Token bucket rate limiter with per-minute and per-hour windows."""

    def __init__(self):
        # sender_key -> deque of timestamps
        self._minute_windows: dict[str, deque] = defaultdict(deque)
        self._hour_windows: dict[str, deque] = defaultdict(deque)

    def check(self, sender_key: str) -> Optional[str]:
        """
        Returns None if allowed, or an error string if rate limited.
        sender_key = f"{channel}:{sender_id}"
        """
        cfg = get_config()
        limits = cfg.security.rate_limit
        now = time.time()

        # Clean and check minute window
        mw = self._minute_windows[sender_key]
        while mw and now - mw[0] > 60:
            mw.popleft()
        if len(mw) >= limits.per_minute:
            return f"Rate limited: {limits.per_minute} messages/minute exceeded"

        # Clean and check hour window
        hw = self._hour_windows[sender_key]
        while hw and now - hw[0] > 3600:
            hw.popleft()
        if len(hw) >= limits.per_hour:
            return f"Rate limited: {limits.per_hour} messages/hour exceeded"

        # Record this message
        mw.append(now)
        hw.append(now)
        return None


class AuthManager:
    def __init__(self, audit_logger=None):
        self.rate_limiter = RateLimiter()
        self.audit_logger = audit_logger

    def _check(self, channel: str, user_id, enabled: bool, allowed: list) -> Optional[str]:
        if not enabled:
            return f"{channel.title()} channel not enabled"
        if allowed and str(user_id) not in [str(u) for u in allowed]:
            self._log_rejection(channel, str(user_id), "not in allowlist")
            return f"User {user_id} not in {channel.title()} allowlist"
        rate_err = self.rate_limiter.check(f"{channel}:{user_id}")
        if rate_err:
            self._log_rejection(channel, str(user_id), rate_err)
            return rate_err
        return None

    def check_discord(self, user_id: str) -> Optional[str]:
        """Returns None if allowed, error string if denied."""
        cfg = get_config()
        return self._check("discord", user_id, cfg.discord.enabled, cfg.discord.allowed_users)

    def check_telegram(self, user_id: int) -> Optional[str]:
        """Returns None if allowed, error string if denied."""
        cfg = get_config()
        return self._check("telegram", user_id, cfg.telegram.enabled, cfg.telegram.allowed_users)

    def _log_rejection(self, channel: str, sender_id: str, reason: str) -> None:
        if self.audit_logger:
            self.audit_logger.log("auth_rejection", channel=channel, sender_id=sender_id, reason=reason)
