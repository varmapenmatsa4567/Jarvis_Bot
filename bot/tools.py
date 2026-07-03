import asyncio
import os
from pathlib import Path

from agents import Agent, function_tool
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.constants import ParseMode

from bot.config import model, WORKSPACE_DIR
from bot.database import load_memories
from bot.state import _last_ui, _run_hashes
from bot.utils import _sanitize_html


def make_agent(mcp_servers, scheduler=None, user_id=None, chat_id=None, chat=None, bot=None):
    memories = load_memories()
    memory_context = ""
    if memories:
        memory_context = "\nRelevant memories about the user:\n" + "\n".join(f"- {m}" for m in memories)

    extra_tools_note = ""
    tools = []

    @function_tool(name_override="custom_list_files")
    async def list_files(path: str = ".") -> str:
        """List files and directories at the given path."""
        p = Path(path)
        if not p.exists():
            return f"Path does not exist: {path}"
        items = []
        for entry in p.iterdir():
            suffix = "/" if entry.is_dir() else ""
            items.append(f"{entry.name}{suffix}")
        return "\n".join(sorted(items))
    tools.append(list_files)

    @function_tool(name_override="custom_run_command")
    async def run_command(command: str) -> str:
        """Run a shell command and return its output. Use this for terminal operations like creating projects, installing packages, etc."""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE_DIR),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            return "Command timed out."
        output = stdout.decode() if stdout else ""
        if stderr:
            output += "\nSTDERR:\n" + stderr.decode()
        if proc.returncode != 0:
            output += f"\nExit code: {proc.returncode}"
        return output.strip()
    tools.append(run_command)

    @function_tool(name_override="custom_run_applescript")
    async def run_applescript(script: str) -> str:
        """Execute AppleScript code. Use this for macOS automation tasks that AppleScript can handle (controlling apps, system dialogs, Finder, etc.)."""
        script_hash = hash(script)
        if script_hash in _run_hashes:
            return "✅ Skipped (already executed this action)."
        _run_hashes.add(script_hash)
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return "AppleScript timed out."
        output = stdout.decode().strip() if stdout else ""
        if stderr:
            err = stderr.decode().strip()
            if err:
                output += f"\nError: {err}"
        if not output:
            return "✅ Action completed successfully."
        return output
    tools.append(run_applescript)

    @function_tool(name_override="custom_press_keys")
    async def press_keys(keys: str) -> str:
        """Press a keyboard shortcut or a single key. FALLBACK — use Peekaboo's keyboard tools (when available via MCP) instead. Examples: "cmd+c" (copy), "cmd+shift+4" (screenshot), "enter", "escape", "tab". Use + to combine modifiers with a key. Modifier names: cmd, shift, option, ctrl."""
        import pyautogui
        keys = keys.replace(" ", "")
        parts = keys.split("+")
        if len(parts) == 1:
            pyautogui.press(parts[0].lower())
        else:
            pyautogui.hotkey(*[p.lower() for p in parts])
        return f"Pressed: {keys}"
    tools.append(press_keys)

    @function_tool(name_override="custom_type_text")
    async def type_text(text: str) -> str:
        """Type a string of text using the keyboard. FALLBACK — use Peekaboo's keyboard tools (when available via MCP) instead."""
        import pyautogui
        pyautogui.typewrite(text, interval=0.05)
        return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"
    tools.append(type_text)

    @function_tool(name_override="custom_click_mouse")
    async def click_mouse(x: int, y: int, button: str = "left") -> str:
        """Click the mouse at screen coordinates (x, y). FALLBACK — use Peekaboo's click/label tools (when available via MCP) instead. Prefer clicking by accessibility label or UI element. button can be 'left', 'right', or 'middle'."""
        import pyautogui
        pyautogui.click(x=x, y=y, button=button)
        return f"Clicked {button} at ({x}, {y})"
    tools.append(click_mouse)

    @function_tool(name_override="custom_move_mouse")
    async def move_mouse(x: int, y: int) -> str:
        """Move the mouse cursor to screen coordinates (x, y). FALLBACK — use Peekaboo's mouse tools (when available via MCP) instead."""
        import pyautogui
        pyautogui.moveTo(x=x, y=y, duration=0.3)
        return f"Moved mouse to ({x}, {y})"
    tools.append(move_mouse)

    @function_tool(name_override="custom_scroll_mouse")
    async def scroll_mouse(clicks: int) -> str:
        """Scroll up or down. FALLBACK — use Peekaboo's scroll tools (when available via MCP) instead. Positive clicks = scroll up, negative = scroll down."""
        import pyautogui
        pyautogui.scroll(clicks)
        direction = "up" if clicks > 0 else "down"
        return f"Scrolled {direction} ({abs(clicks)} clicks)"
    tools.append(scroll_mouse)

    @function_tool(name_override="custom_ask_choice")
    async def ask_choice(question: str, options: list) -> str:
        """Ask the user to pick from a list of options. Use this when you need the user to choose between alternatives. The user's response will arrive in their next message."""
        if chat is None:
            return "Failed: no active chat."
        keyboard = [[InlineKeyboardButton(str(opt), callback_data=f"ch:{i}")] for i, opt in enumerate(options)]
        await chat.send_message(question, reply_markup=InlineKeyboardMarkup(keyboard))
        _last_ui[chat.id] = {"question": question, "options": options}
        return "[STOP]"
    tools.append(ask_choice)

    @function_tool(name_override="custom_ask_text")
    async def ask_text(prompt: str) -> str:
        """Ask the user to type a free-form response. Use this when you need details, clarification, or open-ended input."""
        if chat is None:
            return "Failed: no active chat."
        await chat.send_message(prompt, reply_markup=ForceReply())
        _last_ui[chat.id] = {"question": prompt}
        return "[STOP]"
    tools.append(ask_text)

    @function_tool(name_override="custom_send_screenshot")
    async def send_screenshot(app_name: str = "Google Chrome", caption: str = "") -> str:
        """Take a screenshot of the current screen and send it to the Telegram chat. Specify app_name to bring that app to front first (default: "Google Chrome"). This is the PREFERRED screenshot method — captures what the user sees and sends it directly. If you use Chrome DevTools' take_screenshot instead, always set filePath and then use custom_send_file to send the result."""
        if chat is None:
            return "Failed: no active chat found."
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            activate = await asyncio.create_subprocess_exec(
                "osascript", "-e", f'tell application "{app_name}" to activate',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await activate.wait()
            await asyncio.sleep(0.4)
            proc = await asyncio.create_subprocess_exec(
                "screencapture", "-x", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                return "Screenshot timed out."
            if proc.returncode != 0:
                err = stderr.decode().strip() if stderr else "unknown error"
                return f"Screenshot failed: {err}"
            with open(path, "rb") as f:
                if caption:
                    await chat.send_photo(photo=f, caption=_sanitize_html(caption), parse_mode=ParseMode.HTML)
                else:
                    await chat.send_photo(photo=f)
            return "✅ Screenshot sent to user."
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
    tools.append(send_screenshot)

    @function_tool(name_override="custom_send_file")
    async def send_file(file_path: str, caption: str = "") -> str:
        """Send any file or image from disk to the user. Use this when you need to share a downloaded file, a saved image, a generated document, or any other file with the user. Image files are shown inline."""
        if chat is None:
            return "Failed: no active chat found."
        p = Path(file_path)
        if not p.exists():
            return f"File not found: {file_path}"
        image_exts = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
        try:
            with open(p, "rb") as f:
                if p.suffix.lower() in image_exts:
                    if caption:
                        await chat.send_photo(photo=f, caption=_sanitize_html(caption), parse_mode=ParseMode.HTML)
                    else:
                        await chat.send_photo(photo=f)
                else:
                    if caption:
                        await chat.send_document(document=f, caption=_sanitize_html(caption), parse_mode=ParseMode.HTML)
                    else:
                        await chat.send_document(document=f)
            return f"✅ File sent to user: {p.name}"
        except Exception as e:
            if bot is not None:
                try:
                    with open(p, "rb") as f:
                        if p.suffix.lower() in image_exts:
                            if caption:
                                await bot.send_photo(chat_id=chat.id, photo=f, caption=_sanitize_html(caption), parse_mode=ParseMode.HTML)
                            else:
                                await bot.send_photo(chat_id=chat.id, photo=f)
                        else:
                            if caption:
                                await bot.send_document(chat_id=chat.id, document=f, caption=_sanitize_html(caption), parse_mode=ParseMode.HTML)
                            else:
                                await bot.send_document(chat_id=chat.id, document=f)
                    return f"✅ File sent to user: {p.name}"
                except Exception:
                    pass
            return f"Failed to send file: {e}"
    tools.append(send_file)

    if scheduler and user_id and chat_id:
        @function_tool(name_override="custom_schedule_task")
        async def schedule_task(request: str) -> str:
            """Schedule a task to run in the background. Use this when the user asks to set a reminder, schedule something, send something periodically, or run something automatically. Pass the user's full request as-is."""
            return await scheduler.create_task(request, user_id, chat_id)
        tools.append(schedule_task)
        extra_tools_note = "\n- **Scheduling**: custom_schedule_task — set up reminders and recurring tasks."

    instructions = f"""You are a helpful assistant with browser automation, file system, shell access, macOS control, and task scheduling.

Format your responses using ONLY these Telegram HTML tags (NO other HTML allowed):
• &lt;b&gt;bold&lt;/b&gt; for headings/emphasis
• &lt;i&gt;italic&lt;/i&gt; for subtle emphasis
• &lt;u&gt;underline&lt;/u&gt;
• &lt;s&gt;strikethrough&lt;/s&gt;
• &lt;code&gt;code&lt;/code&gt; for commands/filenames
• &lt;pre&gt;code block&lt;/pre&gt; for multi-line blocks
• &lt;a href="url"&gt;link text&lt;/a&gt; for links
• &lt;span class="tg-spoiler"&gt;spoiler&lt;/span&gt;
Do NOT use: &lt;h1&gt;-&lt;h6&gt;, &lt;p&gt;, &lt;div&gt;, &lt;ul&gt;/&lt;ol&gt;/&lt;li&gt;, &lt;hr&gt;, &lt;br&gt;, &lt;blockquote&gt;, &lt;table&gt;, &lt;style&gt;, or any other HTML tag.

CRITICAL: Once you complete a user's request, DO NOT repeat the same action. If a tool call succeeds, trust the result and move on to producing your final output. Do not "verify" by running the same tool again.

When the user is continuing or correcting a previous request (e.g., "[Previous task interrupted]" in the input), DO NOT start over. First check what page is currently open using the available DevTools tools (list_pages, select_page, snapshot) and continue from the current state. For example, if YouTube is already open and they want a different search query, just type the new query and click search — don't navigate to YouTube again or open a new tab.

Capabilities:
- **Browser**: Chrome DevTools via MCP tools — navigate, click, type, snapshot, screenshot, etc.
- **File system**: read_file, write_file, list_files — read and write files on disk.
- **Shell**: custom_run_command — execute terminal commands.{extra_tools_note}
- **macOS (Peekaboo MCP)**: Peekaboo provides macOS UI automation via Accessibility API — click by label, get UI tree, type text, press keys, scroll, set values, interact with menus/dialogs. **PREFER Peekaboo tools** over pyautogui tools (custom_click_mouse, custom_press_keys, custom_type_text, etc.) — they are labeled "FALLBACK" and should only be used when Peekaboo is unavailable or doesn't work.
- **macOS (AppleScript)**: custom_run_applescript — control macOS apps, Finder, system dialogs.
- **macOS (Keyboard/Mouse - FALLBACK)**: custom_press_keys, custom_type_text, custom_click_mouse, custom_move_mouse, custom_scroll_mouse — pyautogui-based fallbacks when Peekaboo's Accessibility API isn't suitable.
- **Screenshots**: custom_send_screenshot(app_name, caption) — brings the specified app to front, captures the screen, and sends to Telegram. This is the PREFERRED way to send screenshots to the user. Pass the correct app_name (default "Google Chrome"). If you use Chrome DevTools' take_screenshot, always set filePath to save it (e.g. /tmp/shot.png) then use custom_send_file to send it.
- **Files**: custom_send_file — send any file or image on disk to the user.
- **Interactive**: custom_ask_choice (inline buttons for options), custom_ask_text (prompt for free-form input).

Use interactive tools ONLY when you genuinely need more information to proceed:
- If the user needs to pick from a few options, use custom_ask_choice.
- If the user needs to type something, use custom_ask_text.
- IMPORTANT: After calling custom_ask_choice or custom_ask_text, the tool returns "[STOP]". When you see "[STOP]", output NOTHING — end your response silently. The user's answer will come in their next message.
- Do NOT ask unnecessary questions. If the user's request is clear enough to act on, just do it without asking.
- Read the "Previous conversation" context below before responding to maintain continuity.

When the user asks to schedule something, set a reminder, or run something periodically, use the custom_schedule_task tool.

Always complete each step before moving to the next.

NOTE: Peekaboo (macOS Accessibility API) tools can send keyboard/mouse input to specific applications in the background without stealing focus. Use the Peekaboo tools (click by label, UI tree, type text, press key, scroll) for all macOS UI interactions. Only fall back to the "custom_*" pyautogui tools if Peekaboo tools are not available or produce errors.{memory_context}"""

    return Agent(
        name="Assistant",
        instructions=instructions,
        model=model,
        mcp_servers=mcp_servers,
        tools=tools,
        mcp_config={"include_server_in_tool_names": True},
    )
