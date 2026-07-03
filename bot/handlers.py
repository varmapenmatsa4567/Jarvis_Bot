import asyncio
from pathlib import Path

from agents import Runner, MaxTurnsExceeded
from agents.lifecycle import RunHooksBase, RunContextWrapper
from agents.models.interface import ModelResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import ALLOWED_USER_IDS
from bot.state import _last_ui, _run_hashes, _running_tasks
from bot.utils import _sanitize_html, _parse_stop, _cancel_task
from bot.database import add_to_history, get_history_context
from bot.tools import make_agent
from bot.background import update_memories, _update_summary_if_needed



async def run_agent(chat_id: int, user_id: int, text: str, context: ContextTypes.DEFAULT_TYPE, reply_func, chat=None, bot=None):
    global _run_hashes
    _run_hashes.clear()
    scheduler = context.bot_data.get("scheduler")
    history_context = get_history_context(user_id)
    full_text = f"{history_context}\n\nUser: {text}" if history_context else text

    agent = make_agent(
        context.bot_data["mcp_servers"],
        scheduler=scheduler,
        user_id=user_id,
        chat_id=chat_id,
        chat=chat,
        bot=bot,
    )
    progress_msg = None
    try:
        progress_msg = await reply_func("⏳ Working...")

        class _ProgressHooks(RunHooksBase):
            async def on_llm_end(self, _ctx: RunContextWrapper, _agent: Agent, response: ModelResponse) -> None:
                texts = []
                for item in response.output:
                    if getattr(item, "type", None) == "message" and hasattr(item, "content") and isinstance(item.content, list):
                        for part in item.content:
                            if hasattr(part, "text") and part.text:
                                texts.append(part.text)
                if texts:
                    text = "".join(texts).strip()
                    if text:
                        try:
                            await progress_msg.edit_text(_sanitize_html(text), parse_mode=ParseMode.HTML)
                        except Exception:
                            pass

        hooks = _ProgressHooks()
        result = await Runner.run(agent, full_text, max_turns=50, hooks=hooks)
        response = result.final_output.strip() if result.final_output else ""
        if not response or response == "[STOP]":
            try:
                await progress_msg.delete()
            except Exception:
                pass
            return
        add_to_history(user_id, "user", text)
        add_to_history(user_id, "assistant", response)
        asyncio.create_task(update_memories(text, response))
        asyncio.create_task(_update_summary_if_needed(user_id))
    except asyncio.CancelledError:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        raise
    except MaxTurnsExceeded:
        err = "The task required more steps than allowed and couldn't be completed. Try breaking it into smaller steps."
        try:
            await progress_msg.edit_text(_sanitize_html(err), parse_mode=ParseMode.HTML)
        except Exception:
            await reply_func(err)
    except Exception as e:
        import traceback
        traceback.print_exc()
        err = f"Error: {e}"
        try:
            await progress_msg.edit_text(_sanitize_html(err), parse_mode=ParseMode.HTML)
        except Exception:
            await reply_func(err)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    if not user or user.id not in ALLOWED_USER_IDS:
        await update.message.reply_text("Access denied.")
        return

    _cancel_task(chat_id)

    is_stop, rest = _parse_stop(update.message.text)
    if is_stop and not rest:
        await update.message.reply_text("✅ Stopped.")
        return

    query = (f"[Previous task interrupted. Continue from the current state, don't start over. User's instruction: {rest}]\n\nUser: {rest}"
             if is_stop else update.message.text)
    try:
        await update.message.chat.send_action("typing")
        task = asyncio.create_task(
            run_agent(
                chat_id,
                user.id,
                query,
                context,
                lambda t: update.message.reply_text(_sanitize_html(t), parse_mode=ParseMode.HTML),
                chat=update.effective_chat,
                bot=context.bot,
            )
        )
        _running_tasks[chat_id] = task
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        if _running_tasks.get(chat_id) is task:
            del _running_tasks[chat_id]


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat = query.message.chat
    user = query.from_user

    if not user or user.id not in ALLOWED_USER_IDS:
        return

    data = query.data
    if data.startswith("ch:"):
        idx = int(data[3:])
        ui = _last_ui.get(chat.id, {})
        options = ui.get("options", [])
        question = ui.get("question", "")
        choice = options[idx] if 0 <= idx < len(options) else data
        await query.edit_message_text(f"✅ {choice}")
        user_text = f"[User choice: {choice}]\n(Previous question: {question})"
    else:
        return

    _cancel_task(chat.id)
    try:
        await chat.send_action("typing")
        task = asyncio.create_task(
            run_agent(
                chat.id,
                user.id,
                user_text,
                context,
                lambda t: context.bot.send_message(chat_id=chat.id, text=_sanitize_html(t), parse_mode=ParseMode.HTML),
                chat=chat,
                bot=context.bot,
            )
        )
        _running_tasks[chat.id] = task
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        if _running_tasks.get(chat.id) is task:
            del _running_tasks[chat.id]
