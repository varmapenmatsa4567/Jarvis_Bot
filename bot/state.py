from typing import Any
import asyncio

_last_ui: dict[int, dict[str, Any]] = {}
_run_hashes: set[int] = set()
conversation_history: dict[int, list[dict]] = {}
_running_tasks: dict[int, asyncio.Task] = {}
