import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from openai import AsyncOpenAI
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.mcp import MCPServerStdio
from agents import Agent

BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = Path("/Users/chiranjeevip/Developer/Bot_Workspace")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_IDS = [593940008]

os.environ.setdefault("OPENAI_API_KEY", "")

DB_PATH = BASE_DIR / "memories.db"

client = AsyncOpenAI(
    base_url="https://opencode.ai/zen/v1",
    api_key=os.environ.get("OPENCODE_API_KEY", ""),
)

# client = AsyncOpenAI(
#     base_url="http://localhost:11434/v1",
#     api_key="ollama",  # Required by the SDK, ignored by Ollama
# )

model = OpenAIChatCompletionsModel(
    model="big-pickle",
    openai_client=client,
)

filesystem_server = MCPServerStdio(
    name="Filesystem",
    params={
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", str(WORKSPACE_DIR)],
    },
    cache_tools_list=True,
    client_session_timeout_seconds=120,
)

browser_server = MCPServerStdio(
    name="Chrome DevTools",
    params={
        "command": "npx",
        "args": ["-y", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"],
    },
    cache_tools_list=True,
    client_session_timeout_seconds=120,
)

peekaboo_server = MCPServerStdio(
    name="Peekaboo",
    params={
        "command": "npx",
        "args": ["-y", "@steipete/peekaboo", "mcp"],
    },
    cache_tools_list=True,
    client_session_timeout_seconds=120,
    tool_filter={
        "blocked_tool_names": ["see", "capture", "image"],
    },
)

memory_agent = Agent(
    name="Memory Extractor",
    instructions="""Extract NEW enduring facts and preferences about the user from the conversation that are NOT already in the existing memories below.

If there is nothing new to add, output nothing (empty string).

Otherwise, output only concise memory entries, one per line, prefixed with '- '.

CRITICAL - Only extract if ALL of these apply:
1. It reveals a lasting preference, habit, or interest of the user (e.g. "User prefers X over Y", "User likes genre Z")
2. It's a personal fact about the user (e.g. "User works on project X", "User's name is Y")
3. It's a recurring need or constraint (e.g. "User wants output in format X", "User is on macOS")

DO NOT extract:
- Details of a specific one-time task (song names, movie details, URLs, search queries)
- Temporary instructions for the current task
- Generic greetings or small talk
- Facts about third parties, celebrities, or media that the user just mentioned in passing
- Anything that is already covered by an existing memory""",
    model=model,
)

summary_agent = Agent(
    name="Chat Summarizer",
    instructions="""Condense the chat history into a brief summary (2-4 sentences). Capture:
- The user's ongoing goals and projects
- Key decisions and actions taken
- Important preferences or constraints expressed
- Unresolved tasks or pending items

Keep it concise. Focus on information useful for continuing the conversation.""",
    model=model,
)
