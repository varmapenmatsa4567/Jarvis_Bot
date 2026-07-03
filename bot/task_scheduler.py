import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from agents import Agent, Runner, function_tool
from bot.scheduler_models import Task, TaskLog, init_db


class SchedulerEngine:
    def __init__(self, bot_app, mcp_servers, model):
        self.bot_app = bot_app
        self.mcp_servers = mcp_servers
        self.model = model
        self.engine = init_db()
        self.scheduler = AsyncIOScheduler()

        self.parser_agent = Agent(
            name="Task Parser",
            instructions="""You convert natural language scheduling requests into structured JSON.
Output ONLY valid JSON. No markdown, no code blocks, no extra text.

One-time task format:
{
  "type": "once",
  "execute_at": "2026-06-29T10:35:00Z",
  "instruction": "Clear actionable instruction for the AI agent",
  "timezone": "Asia/Kolkata"
}

Recurring task format:
{
  "type": "recurring",
  "cron": "0 8 * * *",
  "instruction": "Clear actionable instruction for the AI agent",
  "timezone": "Asia/Kolkata"
}

Rules:
- execute_at MUST be in UTC with a trailing Z (ISO 8601). Convert the user's
  timezone to UTC yourself. For example, 3:30 PM IST = 10:00 AM UTC = 10:00:00Z.
- For relative times ("after 5 minutes", "in 2 hours"), add to the current UTC
  time and output in ISO 8601 UTC format.
- For recurring patterns, use standard 5-field cron (minute hour day month day_of_week).
- Default timezone to "Asia/Kolkata" unless specified.
- instruction must be a self-contained prompt another AI can execute independently.""",
            model=model,
        )

    async def start(self):
        print("[Scheduler] Starting...")
        self.scheduler.start()
        session = Session(self.engine)
        try:
            tasks = session.query(Task).filter(
                Task.enabled == True,
                Task.status.in_(["pending", "active"]),
            ).all()
            count = 0
            now = datetime.now(timezone.utc)
            for task in tasks:
                if task.type == "once" and task.execute_at:
                    exec_utc = task.execute_at.replace(tzinfo=timezone.utc)
                    if exec_utc < now:
                        print(f"[Scheduler] Skipping past task {task.id}, was due at {exec_utc}")
                        task.status = "missed"
                        continue
                self._register_job(task)
                count += 1
            session.commit()
            print(f"[Scheduler] Restored {count} tasks.")
        finally:
            session.close()

    async def shutdown(self):
        print("[Scheduler] Shutting down...")
        self.scheduler.shutdown(wait=False)

    async def create_task(self, nl_text: str, user_id: int, chat_id: int) -> str:
        try:
            parsed = await self._parse(nl_text)
        except Exception as e:
            print(f"[Scheduler] Parse failed: {e}")
            return f"Couldn't understand that scheduling request: {e}"

        print(f"[Scheduler] Parsed: {json.dumps(parsed)}")

        if parsed.get("type") == "once":
            execute_at = datetime.fromisoformat(parsed["execute_at"].replace("Z", "+00:00"))
            task = Task(
                user_id=user_id,
                chat_id=chat_id,
                type="once",
                execute_at=execute_at,
                instruction=parsed["instruction"],
                timezone=parsed.get("timezone", "Asia/Kolkata"),
                status="pending",
                next_run=execute_at,
            )
        elif parsed.get("type") == "recurring":
            task = Task(
                user_id=user_id,
                chat_id=chat_id,
                type="recurring",
                cron_expression=parsed["cron"],
                instruction=parsed["instruction"],
                timezone=parsed.get("timezone", "Asia/Kolkata"),
                status="active",
            )
        else:
            return f"Invalid task type: {parsed.get('type')}"

        session = Session(self.engine)
        try:
            session.add(task)
            session.commit()
            session.refresh(task)
        finally:
            session.close()

        self._register_job(task)

        if task.type == "once":
            tz = ZoneInfo(task.timezone) if task.timezone else ZoneInfo("UTC")
            local = task.execute_at.replace(tzinfo=timezone.utc).astimezone(tz)
            when = local.strftime(f"%Y-%m-%d %H:%M")
            return f"✅ One-time task scheduled at {when} ({task.timezone}).\nInstruction: {task.instruction}"
        else:
            return f"✅ Recurring task scheduled (`{task.cron_expression}`).\nInstruction: {task.instruction}"

    async def _parse(self, nl_text: str) -> dict:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = await Runner.run(
            self.parser_agent,
            f"Current time (UTC): {now}\nUser request: {nl_text}",
            max_turns=1,
        )
        raw = result.final_output.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0]
        return json.loads(raw)

    def _register_job(self, task: Task):
        job_id = f"task_{task.id}"
        if task.type == "once" and task.execute_at:
            run_date = task.execute_at.replace(tzinfo=timezone.utc)
            print(f"[Scheduler] Registering one-time job {job_id}, due at {run_date}")
            trigger = DateTrigger(run_date=run_date)
            self.scheduler.add_job(
                self._execute,
                trigger,
                args=[task.id],
                id=job_id,
                replace_existing=True,
            )
        elif task.type == "recurring" and task.cron_expression:
            parts = task.cron_expression.strip().split()
            if len(parts) != 5:
                print(f"[Scheduler] Invalid cron for task {task.id}: {task.cron_expression}")
                return
            print(f"[Scheduler] Registering recurring job {job_id}, cron={task.cron_expression}")
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
                timezone=task.timezone or "Asia/Kolkata",
            )
            self.scheduler.add_job(
                self._execute,
                trigger,
                args=[task.id],
                id=job_id,
                replace_existing=True,
            )

        jobs = self.scheduler.get_jobs()
        print(f"[Scheduler] APScheduler now has {len(jobs)} jobs: {[j.id for j in jobs]}")

    async def _execute(self, task_id: int):
        print(f"[Scheduler] _execute called for task {task_id}")
        session = Session(self.engine)
        try:
            task = session.get(Task, task_id)
            if not task:
                print(f"[Scheduler] Task {task_id} not found in DB")
                return
            if not task.enabled or task.status == "cancelled":
                print(f"[Scheduler] Task {task_id} not enabled or cancelled")
                return

            print(f"[Scheduler] Executing task {task_id}: {task.instruction[:60]}")
            task.status = "running"
            session.commit()

            log = TaskLog(task_id=task_id, status="running")
            session.add(log)
            session.commit()
            session.refresh(log)
            log_id = log.id
            print(f"[Scheduler] Task {task_id}: created log {log_id}")

            try:
                worker = self._make_worker_agent()
                print(f"[Scheduler] Task {task_id}: running worker agent...")
                result = await Runner.run(worker, task.instruction, max_turns=50)
                output = result.final_output
                print(f"[Scheduler] Task {task_id}: agent output={output[:100]}")

                log.status = "completed"
                log.finished_at = datetime.utcnow()
                log.output = output

                task.status = "completed" if task.type == "once" else "active"
                task.last_run = datetime.utcnow()
                task.retry_count = 0

                print(f"[Scheduler] Task {task_id}: sending completion notification")
                await self._notify_user(task.chat_id, output)

            except Exception as e:
                print(f"[Scheduler] Task {task_id} execution error: {e}")
                task.retry_count = (task.retry_count or 0) + 1
                log.status = "failed"
                log.finished_at = datetime.utcnow()
                log.error = str(e)

                if task.retry_count >= task.max_retries:
                    task.status = "failed"
                    print(f"[Scheduler] Task {task_id}: retries exhausted, marking failed")
                else:
                    task.status = "pending"
                    print(f"[Scheduler] Task {task_id}: will retry ({task.retry_count}/{task.max_retries})")

                await self._notify_user(
                    task.chat_id,
                    f"Task '{task.instruction[:60]}...' failed: {e}",
                )

            session.commit()
            print(f"[Scheduler] Task {task_id}: done, status={task.status}")

        except Exception as e:
            print(f"[Scheduler] Task {task_id}: outer exception: {e}")
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()

    async def _notify_user(self, chat_id: int, text: str):
        try:
            await self.bot_app.bot.send_message(
                chat_id=chat_id,
                text=text,
            )
            print(f"[Scheduler] Notification sent to chat {chat_id}")
        except Exception as e:
            print(f"[Scheduler] Failed to send notification to {chat_id}: {e}")

    def _make_worker_agent(self):
        @function_tool
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

        @function_tool
        async def run_command(command: str) -> str:
            """Run a shell command and return its output."""
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/Users/chiranjeevip/Developer/Bot_Workspace",
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

        return Agent(
            name="Worker",
            instructions="""You are a background task executor.
- Use tools if needed to complete the task (browse, shell, filesystem).
- Output ONLY the final result. No explanations, no tool logs, no commentary.
- For reminders, just state the reminder message directly.""",
            model=self.model,
            mcp_servers=self.mcp_servers,
            tools=[list_files, run_command],
            mcp_config={"include_server_in_tool_names": True},
        )
