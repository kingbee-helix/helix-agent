"""
Helix Telegram Adapter
python-telegram-bot async polling.
DM + group chat support. Typing indicator while processing. Slash commands intercepted.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

from channels.base import ChannelAdapter
from channels.base import MessageHandler as HelixMessageHandler
from channels.slash_commands import handle_slash, wrap_agent_slash
from security.auth import AuthManager
from security.input_validator import sanitize_for_context
from security.secrets import get_secret
from core.config import get_config

logger = logging.getLogger("helix.telegram")

DEBOUNCE_SECONDS = 1.5


class TelegramAdapter(ChannelAdapter):
    def __init__(self, handler: HelixMessageHandler, auth: AuthManager, agent_loop, session_manager, audit_logger=None):
        super().__init__(handler)
        self.auth = auth
        self.agent_loop = agent_loop
        self.session_manager = session_manager
        self.audit_logger = audit_logger
        self._app = None
        self._debounce_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        token = get_secret("TELEGRAM_TOKEN")
        if not token:
            logger.warning("TELEGRAM_TOKEN not set — Telegram disabled")
            return

        self._app = ApplicationBuilder().token(token).build()
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        # Also handle /commands as text (Telegram sends them as COMMAND type)
        self._app.add_handler(
            MessageHandler(filters.COMMAND, self._on_command)
        )

        await self._app.initialize()
        await self._app.start()
        asyncio.create_task(self._app.updater.start_polling())
        logger.info("Telegram adapter started (polling)")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_message(self, recipient_id: str, text: str, **kwargs) -> None:
        if not self._app:
            return
        try:
            for part in self._split_message(text):
                await self._app.bot.send_message(
                    chat_id=int(recipient_id),
                    text=part,
                    parse_mode=None,  # Plain text to avoid markdown parse errors
                )
        except Exception as e:
            logger.error(f"Telegram send error to {recipient_id}: {e}")

    async def _on_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle Telegram /command messages."""
        if not update.message or not update.effective_user:
            return
        # Rebuild as slash command (Telegram strips the /)
        content = update.message.text or ""
        await self._handle_update(update, content)

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        content = update.message.text or ""
        await self._handle_update(update, content)

    async def _handle_update(self, update: Update, content: str) -> None:
        cfg = get_config()
        if not cfg.telegram.enabled:
            return

        user = update.effective_user
        user_id = user.id
        sender_name = user.full_name or str(user_id)

        # Auth check
        deny_reason = self.auth.check_telegram(user_id)
        if deny_reason:
            logger.warning(f"Telegram auth denied {user_id}: {deny_reason}")
            return

        if not content:
            return

        # Audit log
        if self.audit_logger:
            self.audit_logger.log("message_received", channel="telegram", sender_id=str(user_id), sender_name=sender_name, content_preview=content[:200])

        # Debounce
        debounce_key = f"telegram:{user_id}"
        if debounce_key in self._debounce_tasks:
            self._debounce_tasks[debounce_key].cancel()

        async def _process():
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await self._process_message(update, content, user_id, sender_name)

        task = asyncio.create_task(_process())
        self._debounce_tasks[debounce_key] = task

    async def _process_message(
        self,
        update: Update,
        content: str,
        user_id: int,
        sender_name: str,
    ) -> None:
        channel = "telegram"
        peer = str(user_id)

        async def send_reply(text: str) -> None:
            try:
                for part in self._split_message(text):
                    await update.message.reply_text(part)
                if self.audit_logger:
                    self.audit_logger.log("message_sent", channel="telegram", recipient_id=str(user_id), content_preview=text[:200])
            except Exception as e:
                logger.error(f"Telegram reply error: {e}")

        # Slash command?
        if content.startswith("/"):
            handled = await handle_slash(
                content, channel, peer,
                self.session_manager, self.agent_loop,
                send_reply,
            )
            if handled:
                return
            content = wrap_agent_slash(content)

        # Injection check
        clean_content, warnings = sanitize_for_context(content)
        if warnings:
            for w in warnings:
                logger.warning(f"[INJECTION] Telegram {user_id}: {w}")
                if self.audit_logger:
                    self.audit_logger.log("injection_detected", channel="telegram", sender_id=str(user_id), pattern=w, content_preview=content[:200])
            clean_content = "[SECURITY WARNING: Possible injection attempt detected]\n\n" + clean_content

        # Run agent loop with persistent typing indicator (refreshes every 4s — Telegram expires it after ~5s)
        stop_typing = asyncio.Event()

        async def _keep_typing() -> None:
            while not stop_typing.is_set():
                try:
                    await update.message.reply_chat_action(ChatAction.TYPING)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(stop_typing.wait()), timeout=4.0)
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

