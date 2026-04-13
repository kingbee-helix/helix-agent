"""
Helix Channel Base
Abstract adapter interface for channel implementations.
"""

from typing import Optional, Callable, Awaitable
from abc import ABC, abstractmethod


# Type alias for the message handler
MessageHandler = Callable[..., Awaitable[Optional[str]]]


class ChannelAdapter(ABC):
    """Abstract base for channel adapters."""

    def __init__(self, handler: MessageHandler):
        self.handler = handler

    @abstractmethod
    async def start(self) -> None:
        """Start the channel listener."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel listener."""

    @abstractmethod
    async def send_message(self, recipient_id: str, text: str, **kwargs) -> None:
        """Send a message to a recipient."""

    def _split_message(self, text: str, limit: int = 1900) -> list[str]:
        """Split long messages at paragraph boundaries."""
        if len(text) <= limit:
            return [text]

        parts = []
        while len(text) > limit:
            # Find last paragraph break before limit
            split_at = text.rfind("\n\n", 0, limit)
            if split_at == -1:
                # Fall back to last newline
                split_at = text.rfind("\n", 0, limit)
            if split_at == -1:
                # Hard split
                split_at = limit
            parts.append(text[:split_at].strip())
            text = text[split_at:].strip()
        if text:
            parts.append(text)
        return parts
