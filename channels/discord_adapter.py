"""
Helix Discord Adapter
discord.py 2.7.1 integration.
DMs + guild channel support. AllowList enforced. Slash commands intercepted.
V's emoji ⚡ used for acknowledgment reaction.
Status indicators: thinking... → working on it... → reply.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import Message, DMChannel, TextChannel

from channels.base import ChannelAdapter, InboundMessage, MessageHandler
from channels.slash_commands import handle_slash, HARNESS_COMMANDS, AGENT_COMMANDS
from security.auth import AuthManager
from security.input_validator import sanitize_for_context
from security.secrets import get_secret
from core.config import get_config
from core.file_handler import validate_file, save_file, cleanup_file, build_file_context

logger = logging.getLogger("helix.discord")

DEBOUNCE_SECONDS = 1.5
V_EMOJI = "⚡"


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

        # Handle file attachments
        attachment_info = None
        if message.attachments:
            att = message.attachments[0]
            error = validate_file(att.filename, att.size)
            if error:
                await message.channel.send(error)
                return
            attachment_info = {"filename": att.filename, "attachment": att}

        if not content and not attachment_info:
            return

        # Auth check
        deny_reason = self.auth.check_discord(sender_id)
        if deny_reason:
            logger.warning(f"Discord auth denied {sender_id}: {deny_reason}")
            return

        # Audit log
        if self.audit_logger:
            self.audit_logger.log_message_received("discord", sender_id, sender_name, content[:200])

        # Debounce (collect messages within 1.5s window)
        debounce_key = f"discord:{sender_id}"
        if debounce_key in self._debounce_tasks:
            self._debounce_tasks[debounce_key].cancel()

        async def _process():
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await self._process_message(message, content, sender_id, sender_name, is_dm, attachment_info)

        task = asyncio.create_task(_process())
        self._debounce_tasks[debounce_key] = task

    async def _process_message(
        self,
        message: Message,
        content: str,
        sender_id: str,
        sender_name: str,
        is_dm: bool,
        attachment_info: Optional[dict] = None,
    ) -> None:
        # React to acknowledge
        try:
            await message.add_reaction(V_EMOJI)
        except Exception:
            pass

        channel = "discord_dm" if is_dm else "discord_guild"
        peer = sender_id

        # ── Harness slash commands: instant response, no status indicators ──
        async def send_slash_reply(text: str) -> None:
            try:
                await message.remove_reaction(V_EMOJI, self._client.user)
            except Exception:
                pass
            parts = self._split_message(text)
            if is_dm:
                for part in parts:
                    await self._send_to_channel(message.channel, part)
            else:
                await message.reply(parts[0], mention_author=False, suppress_embeds=True)
                for part in parts[1:]:
                    await message.channel.send(part, suppress_embeds=True)
            if self.audit_logger:
                self.audit_logger.log_message_sent("discord", sender_id, text[:200])

        if content.startswith("/"):
            handled = await handle_slash(
                content, channel, peer,
                self.session_manager, self.agent_loop,
                send_slash_reply,
            )
            if handled:
                return
            # Agent-handled slash: wrap with framing
            content = self._wrap_agent_slash(content)

        # ── Injection check ──
        clean_content, warnings = sanitize_for_context(content)
        if warnings:
            for w in warnings:
                logger.warning(f"[INJECTION] Discord {sender_id}: {w}")
                if self.audit_logger:
                    self.audit_logger.log_injection_detected("discord", sender_id, w, content[:200])
            clean_content = "[SECURITY WARNING: Possible injection attempt detected]\n\n" + clean_content

        # ── File attachment handling ──
        file_path = None
        if attachment_info:
            try:
                data = await attachment_info["attachment"].read()
                file_path = save_file(attachment_info["filename"], data)
                if file_path:
                    clean_content = build_file_context(file_path, clean_content)
                else:
                    await send_slash_reply("Failed to process the attached file. Please try again.")
                    return
            except Exception as e:
                logger.error(f"Discord file download error: {e}")
                await send_slash_reply("Failed to download the attached file. Please try again.")
                return

        # ── Status indicators: thinking... → working on it... → reply ──
        status_msg = None
        status_msg_disposed = False  # Track whether status_msg was edited/deleted
        try:
            status_msg = await message.reply("thinking...", mention_author=False)
        except Exception:
            try:
                status_msg = await message.channel.send("thinking...")
            except Exception:
                pass

        # Native "V is typing..." indicator (refreshes every 9s — Discord expires after ~10s)
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

        # Switch to "working on it..." after 4 seconds
        async def _switch_status() -> None:
            await asyncio.sleep(4)
            if status_msg and not stop_typing.is_set():
                try:
                    await status_msg.edit(content="working on it...")
                except Exception:
                    pass

        switch_task = asyncio.create_task(_switch_status())

        async def send_reply(text: str) -> None:
            nonlocal status_msg_disposed
            try:
                await message.remove_reaction(V_EMOJI, self._client.user)
            except Exception:
                pass
            parts = self._split_message(text)
            if status_msg:
                try:
                    await status_msg.edit(content=parts[0])
                    status_msg_disposed = True
                except Exception:
                    # Edit failed — delete placeholder then send fresh
                    try:
                        await status_msg.delete()
                        status_msg_disposed = True
                    except Exception:
                        pass
                    if is_dm:
                        await self._send_to_channel(message.channel, parts[0])
                    else:
                        await message.reply(parts[0], mention_author=False, suppress_embeds=True)
            else:
                if is_dm:
                    await self._send_to_channel(message.channel, parts[0])
                else:
                    await message.reply(parts[0], mention_author=False, suppress_embeds=True)
            for part in parts[1:]:
                await message.channel.send(part, suppress_embeds=True)
            if self.audit_logger:
                self.audit_logger.log_message_sent("discord", sender_id, text[:200])

        try:
            response_parts = []
            async for chunk in self.agent_loop.run(channel, peer, clean_content):
                response_parts.append(chunk)
            full_response = "".join(response_parts)
            if full_response.strip() and full_response.strip() != "NO_REPLY":
                await send_reply(full_response)
            else:
                # NO_REPLY: clean up silently
                try:
                    await message.remove_reaction(V_EMOJI, self._client.user)
                except Exception:
                    pass
                if status_msg:
                    try:
                        await status_msg.delete()
                        status_msg_disposed = True
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Agent loop error: {e}")
            await send_reply(f"Something went wrong. {e}")
        finally:
            stop_typing.set()
            typing_task.cancel()
            switch_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            try:
                await switch_task
            except asyncio.CancelledError:
                pass
            # Safety net: if status_msg was never disposed (e.g. delete failed silently),
            # make one final attempt to remove the placeholder
            if not status_msg_disposed and status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            cleanup_file(file_path)

    def _wrap_agent_slash(self, command_str: str) -> str:
        parts = command_str.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/think":
            level = args or "normal"
            return f"[Deep thinking mode: {level}] Please think carefully and thoroughly about the following, then provide your response."
        elif cmd == "/do":
            return f"[Task request] Please complete the following task:\n\n{args}"
        elif cmd == "/remember":
            return f"[Memory request] Please save the following to your memory:\n\n{args}"
        elif cmd == "/forget":
            return f"[Memory request] Please remove the following from your memory if you have it:\n\n{args}"
        return command_str
