import asyncio
import subprocess

from agents.mcp import MCPServerManager
from telegram.ext import Application

from bot.config import browser_server, filesystem_server, peekaboo_server, model
from bot.task_scheduler import SchedulerEngine


async def post_init(app: Application):
    existing = subprocess.run(
        ["lsof", "-i", "tcp:9222"],
        capture_output=True, text=True, timeout=5,
    )
    if "Google Chrome" not in existing.stdout:
        chrome_proc = await asyncio.create_subprocess_exec(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "--remote-debugging-port=9222",
            "--user-data-dir=/tmp/chrome-mcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        app.bot_data["chrome_process"] = chrome_proc
        await asyncio.sleep(2)
    else:
        app.bot_data["chrome_process"] = None

    manager = await MCPServerManager([browser_server, filesystem_server, peekaboo_server]).__aenter__()
    app.bot_data["mcp_manager"] = manager
    app.bot_data["mcp_servers"] = manager.active_servers

    scheduler = SchedulerEngine(
        bot_app=app,
        mcp_servers=app.bot_data["mcp_servers"],
        model=model,
    )
    await scheduler.start()
    app.bot_data["scheduler"] = scheduler


async def post_shutdown(app: Application):
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        await scheduler.shutdown()

    manager = app.bot_data.get("mcp_manager")
    if manager:
        await manager.__aexit__(None, None, None)

    chrome_proc = app.bot_data.get("chrome_process")
    if chrome_proc is not None and chrome_proc.returncode is None:
        chrome_proc.terminate()
        try:
            await asyncio.wait_for(chrome_proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            chrome_proc.kill()
            await chrome_proc.wait()
