import sqlite3

from bot.config import DB_PATH
from bot.state import conversation_history


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_summaries (
        user_id INTEGER PRIMARY KEY,
        summary TEXT NOT NULL DEFAULT '',
        message_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    return conn


def add_to_history(user_id: int, role: str, content: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )
    conn.commit()
    conn.close()
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": role, "content": content})


def get_history_context(user_id: int) -> str:
    conn = get_db()
    row = conn.execute(
        "SELECT summary, message_count FROM chat_summaries WHERE user_id=?",
        (user_id,),
    ).fetchone()
    rows = conn.execute(
        "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT 15",
        (user_id,),
    ).fetchall()
    conn.close()

    parts = []
    if row and row[0]:
        parts.append(f"Chat Summary (older history):\n{row[0]}")
    if rows:
        recent = []
        for r in reversed(rows):
            prefix = "User" if r[0] == "user" else "Bot"
            recent.append(f"{prefix}: {r[1]}")
        parts.append("Recent History:\n" + "\n".join(recent))
    return "\n\n".join(parts) if parts else ""


def load_all_memories() -> list[str]:
    conn = get_db()
    rows = conn.execute("SELECT text FROM memories").fetchall()
    conn.close()
    return [row[0] for row in rows]


def load_memories() -> list[str]:
    conn = get_db()
    rows = conn.execute("SELECT text FROM memories ORDER BY id DESC LIMIT 30").fetchall()
    conn.close()
    return [row[0] for row in reversed(rows)]


def add_memories(entries: list[str]):
    conn = get_db()
    conn.executemany("INSERT INTO memories (text) VALUES (?)", [(e,) for e in entries])
    conn.commit()
    conn.close()
