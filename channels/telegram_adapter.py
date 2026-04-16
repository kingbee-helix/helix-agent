"""
Helix Telegram Adapter
python-telegram-bot async polling.
DM + group chat support. Typing indicator while processing. Slash commands intercepted.
File attachments supported (documents and photos).
"""

import asyncio
import logging
from typing import Optional

from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

from channels.base import ChannelAdapter, InboundMessage
from channels.base import MessageHandler as HelixMessageHandler
from channels.slash_commands import handle_slash, HARNESS_COMMANDS, AGENT_COMMANDS
from security.auth import AuthManager
from security.input_validator import sanitize_for_context
from security.secrets import get_secret
from core.config import get_config
from core.file_handler import validate_file, save_file, cleanup_file, build_file_context

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
        # Handle file attachments
        self._app.add_handler(
            MessageHandler(filters.Document.ALL, self._on_document)
        )
        self._app.add_handler(
            MessageHandler(filters.PHOTO, self._on_photo)
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
        content = update.message.text or ""
        await self._handle_update(update, content)

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        content = update.message.text or ""
        await self._handle_update(update, content)

    async def _on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle file/document attachments."""
        if not update.message or not update.effective_user:
            return
        doc = update.message.document
        caption = update.message.caption or ""
        error = validate_file(doc.file_name or "file", doc.file_size or 0)
        if error:
            await update.message.reply_text(error)
            return
        attachment_info = {
            "file_id": doc.file_id,
            "filename": doc.file_name or f"document_{doc.file_id[:8]}",
        }
        await self._handle_update(update, caption, attachment_info=attachment_info)

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo attachments — use largest available resolution."""
        if not update.message or not update.effective_user:
            return
        photo = update.message.photo[-1]  # Largest resolution
        caption = update.message.caption or ""
        error = validate_file("photo.jpg", photo.file_size or 0)
        if error:
            await update.message.reply_text(error)
            return
        attachment_info = {
            "file_id": photo.file_id,
            "filename": f"photo_{photo.file_id[:8]}.jpg",
        }
        await self._handle_update(update, caption, attachment_info=attachment_info)

    async def _handle_update(self, update: Update, content: str, attachment_info: Optional[dict] = None) -> None:
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

        if not content and not attachment_info:
            return

        # Audit log
        if self.audit_logger:
            self.audit_logger.log_message_received("telegram", str(user_id), sender_name, content[:200])

        # Debounce
        debounce_key = f"telegram:{user_id}"
        if debounce_key in self._debounce_tasks:
            self._debounce_tasks[debounce_key].cancel()

        async def _process():
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await self._process_message(update, content, user_id, sender_name, attachment_info)

        task = asyncio.create_task(_process())
        self._debounce_tasks[debounce_key] = task

    async def _process_message(
        self,
        update: Update,
        content: str,
        user_id: int,
        sender_name: str,
        attachment_info: Optional[dict] = None,
    ) -> None:
        chat_id = update.effective_chat.id
        channel = "telegram"
        peer = str(user_id)

        async def send_reply(text: str) -> None:
            try:
                for part in self._split_message(text):
                    await update.message.reply_text(part)
                if self.audit_logger:
                    self.audit_logger.log_message_sent("telegram", str(user_id), text[:200])
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
            content = self._wrap_agent_slash(content)

        # Injection check
        clean_content, warnings = sanitize_for_context(content)
        if warnings:
            for w in warnings:
                logger.warning(f"[INJECTION] Telegram {user_id}: {w}")
                if self.audit_logger:
                    self.audit_logger.log_injection_detected("telegram", str(user_id), w, content[:200])
            clean_content = "[SECURITY WARNING: Possible injection attempt detected]\n\n" + clean_content

        # ── File attachment handling ──
        file_path = None
        if attachment_info:
            try:
                tg_file = await self._app.bot.get_file(attachment_info["file_id"])
                data = bytes(await tg_file.download_as_bytearray())
                file_path = save_file(attachment_info["filename"], data)
                if file_path:
                    clean_content = build_file_context(file_path, clean_content)
                else:
                    await send_reply("Failed to process the attached file. Please try again.")
                    return
            except Exception as e:
                logger.error(f"Telegram file download error: {e}")
                await send_reply("Failed to download the attached file. Please try again.")
                return

        # Run agent loop with persistent typing indicator (refreshes every 4s — Telegram expires after ~5s)
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
            await send_reply(f"Something went wrong. {e}")
        finally:
            stop_typing.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
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
