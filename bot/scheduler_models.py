from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text
from sqlalchemy.orm import declarative_base, Session

from bot.config import BASE_DIR

SCHEDULER_DB_PATH = BASE_DIR / "scheduler.db"
Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    chat_id = Column(Integer, nullable=False)
    type = Column(String(10), nullable=False)
    execute_at = Column(DateTime, nullable=True)
    cron_expression = Column(String(100), nullable=True)
    timezone = Column(String(50), default="UTC")
    instruction = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    status = Column(String(20), default="pending")
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)


def init_db():
    engine = create_engine(f"sqlite:///{SCHEDULER_DB_PATH}")
    Base.metadata.create_all(engine)
    return engine
