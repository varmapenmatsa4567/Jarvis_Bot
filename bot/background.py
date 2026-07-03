from agents import Runner

from bot.config import memory_agent, summary_agent
from bot.database import get_db, load_all_memories, add_memories


async def _update_summary_if_needed(user_id: int):
    try:
        conn = get_db()
        total = conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE user_id=?", (user_id,),
        ).fetchone()[0]
        row = conn.execute(
            "SELECT summary, message_count FROM chat_summaries WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()

        old_summary = row[0] if row else ""
        last_count = row[1] if row else 0
        if total - last_count < 20:
            return

        conn = get_db()
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, total - last_count),
        ).fetchall()
        conn.close()

        messages_text = "\n".join(
            f"{'User' if r[0] == 'user' else 'Bot'}: {r[1]}"
            for r in reversed(rows)
        )
        prompt = f"Previous summary:\n{old_summary if old_summary else 'None'}\n\nRecent messages to incorporate:\n{messages_text}"
        result = await Runner.run(summary_agent, prompt, max_turns=1)
        new_summary = result.final_output.strip()
        if new_summary:
            conn = get_db()
            conn.execute(
                """INSERT INTO chat_summaries (user_id, summary, message_count)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       summary=excluded.summary,
                       message_count=excluded.message_count,
                       updated_at=datetime('now')""",
                (user_id, new_summary, total),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


async def update_memories(user_msg: str, assistant_msg: str):
    try:
        existing = load_all_memories()
        existing_block = "\n".join(f"- {m}" for m in existing) if existing else "None"
        prompt = f"""Existing memories:
{existing_block}

New conversation:
User: {user_msg}
Assistant: {assistant_msg}"""
        result = await Runner.run(memory_agent, prompt, max_turns=1)
        entries = [line.strip("- ").strip() for line in result.final_output.split("\n") if line.strip().startswith("- ")]
        entries = [e for e in entries if not e.lower().startswith(("no new", "nothing", "the user didn", "no facts", "no preferences", "the conversation didn", "user didn"))]
        if entries:
            add_memories(entries)
    except Exception:
        pass
