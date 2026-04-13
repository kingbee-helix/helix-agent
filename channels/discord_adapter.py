"""
Helix Discord Adapter
discord.py 2.7.1 integration.
DMs + guild channel support. AllowList enforced. Slash commands intercepted.
Agent emoji used for acknowledgment reaction.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import Message, DMChannel

from channels.base import ChannelAdapter, MessageHandler
from channels.slash_commands import handle_slash, wrap_agent_slash
from security.auth import AuthManager
from security.input_validator import sanitize_for_context
from security.secrets import get_secret
from core.config import get_config

logger = logging.getLogger("helix.discord")

DEBOUNCE_SECONDS = 1.5
AGENT_EMOJI = "🧬"


class DiscordAdapter(ChannelAdapter):
    def __init__(self, handler: MessageHandler, auth: AuthManager, agent_loop, session_manager, audit_logger=None):
        super().__init__(handler)
        self.auth = auth
        self.agent_loop = agent_loop
        self.session_manager = session_manager
        self.audit_logger = audit_logger
        self._client: Optional[discord.Client] = None
        self._debounce_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        token = get_secret("DISCORD_TOKEN")
        if not token:
            logger.warning("DISCORD_TOKEN not set — Discord disabled")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        intents.guild_messages = True

        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            logger.info(f"Discord connected as {self._client.user} ({self._client.user.id})")

        @self._client.event
        async def on_message(message: Message):
            await self._on_message(message)

        asyncio.create_task(self._client.start(token))

    async def stop(self) -> None:
        if self._client:
            await self._client.close()

    async def send_message(self, recipient_id: str, text: str, **kwargs) -> None:
        """Send a DM to a user by snowflake ID."""
        if not self._client:
            return
        try:
            user = await self._client.fetch_user(int(recipient_id))
            dm = await user.create_dm()
            for part in self._split_message(text):
                # Suppress embeds by wrapping URLs
                await dm.send(part, suppress_embeds=True)
        except Exception as e:
            logger.error(f"Failed to send Discord DM to {recipient_id}: {e}")

    async def _send_to_channel(self, channel_obj, text: str) -> None:
        for part in self._split_message(text):
            await channel_obj.send(part, suppress_embeds=True)

    async def _on_message(self, message: Message) -> None:
        cfg = get_config()
        if not cfg.discord.enabled:
            return

        # Ignore self
        if message.author == self._client.user:
            return

        sender_id = str(message.author.id)
        sender_name = str(message.author.display_name)
        content = message.content or ""
        is_dm = isinstance(message.channel, DMChannel)

        # In guild channels, only respond if mentioned or in designated channel
        if not is_dm:
            channel_id = str(message.channel.id)
            guild_channels = cfg.discord.guild_channels
            mentioned = self._client.user.mentioned_in(message)

            if guild_channels and channel_id not in guild_channels:
                return
            if cfg.discord.mention_only and not mentioned:
                return

            # Strip bot mention from content
            if mentioned:
                content = content.replace(f"<@{self._client.user.id}>", "").strip()
                content = content.replace(f"<@!{self._client.user.id}>", "").strip()

        if not content:
            return

        # Auth check
        deny_reason = self.auth.check_discord(sender_id)
        if deny_reason:
            logger.warning(f"Discord auth denied {sender_id}: {deny_reason}")
            return

        # Audit log
        if self.audit_logger:
            self.audit_logger.log("message_received", channel="discord", sender_id=sender_id, sender_name=sender_name, content_preview=content[:200])

        # Debounce (collect messages within 1.5s window)
        debounce_key = f"discord:{sender_id}"
        if debounce_key in self._debounce_tasks:
            self._debounce_tasks[debounce_key].cancel()

        async def _process():
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await self._process_message(message, content, sender_id, sender_name, is_dm)

        task = asyncio.create_task(_process())
        self._debounce_tasks[debounce_key] = task

    async def _process_message(
        self,
        message: Message,
        content: str,
        sender_id: str,
        sender_name: str,
        is_dm: bool,
    ) -> None:
        # React to acknowledge
        try:
            await message.add_reaction(AGENT_EMOJI)
        except Exception:
            pass

        channel = "discord_dm" if is_dm else "discord_guild"
        peer = sender_id

        async def send_reply(text: str) -> None:
            # Remove AGENT_EMOJI reaction before responding
            try:
                await message.remove_reaction(AGENT_EMOJI, self._client.user)
            except Exception:
                pass
            parts = self._split_message(text)
            if is_dm:
                for part in parts:
                    await self._send_to_channel(message.channel, part)
            else:
                # Reply to first part, then send the rest as follow-ups
                await message.reply(parts[0], mention_author=False, suppress_embeds=True)
                for part in parts[1:]:
                    await message.channel.send(part, suppress_embeds=True)
            if self.audit_logger:
                self.audit_logger.log("message_sent", channel="discord", recipient_id=sender_id, content_preview=text[:200])

        # Slash command?
        if content.startswith("/"):
            handled = await handle_slash(
                content, channel, peer,
                self.session_manager, self.agent_loop,
                send_reply,
            )
            if handled:
                return
            # Agent-handled slash: wrap with framing
            content = wrap_agent_slash(content)

        # Injection check
        clean_content, warnings = sanitize_for_context(content)
        if warnings:
            for w in warnings:
                logger.warning(f"[INJECTION] Discord {sender_id}: {w}")
                if self.audit_logger:
                    self.audit_logger.log("injection_detected", channel="discord", sender_id=sender_id, pattern=w, content_preview=content[:200])
            clean_content = "[SECURITY WARNING: Possible injection attempt detected]\n\n" + clean_content

        # Run agent loop with persistent typing indicator (refreshes every 9s — Discord expires it after ~10s)
        stop_typing = asyncio.Event()

        async def _keep_typing() -> None:
            while not stop_typing.is_set():
                try:
                    await message.channel.trigger_typing()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(stop_typing.wait()), timeout=9.0)
                except asyncio.TimeoutError:
                    pass

        typing_task = asyncio.create_task(_keep_typing())
        try:
            response_parts = []
            async for chunk in self.agent_loop.run(channel, peer, clean_content):
                response_parts.append(chunk)
            full_response = "".join(response_parts)
            if full_response.strip() and full_response.strip() != "NO_REPLY":
                await send_reply(full_response)
            else:
                # Remove reaction silently on NO_REPLY
                try:
                    await message.remove_reaction(AGENT_EMOJI, self._client.user)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Agent loop error: {e}")
            await send_reply("Something went wrong. Please try again.")
        finally:
            stop_typing.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

