import sqlite3
import uuid
import json
from datetime import datetime
from typing import List, Optional
from contextlib import contextmanager

from src.core.models import (
    Task, TaskEvent, TaskStatus, TaskType, EventType
)
from src.orchestrator.state_machine import TaskStateMachine, InvalidTransitionError


class TaskNotFoundError(Exception):
    pass


class TaskManager:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    agent_name TEXT,
                    repository TEXT NOT NULL,
                    branch TEXT,
                    priority INTEGER DEFAULT 0,
                    requires_confirmation BOOLEAN DEFAULT 1,
                    result_summary TEXT,
                    files_modified TEXT DEFAULT '[]',
                    errors TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
            """)
            conn.commit()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            user_message=row["user_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=TaskStatus(row["status"]),
            task_type=TaskType(row["task_type"]),
            agent_name=row["agent_name"],
            repository=row["repository"],
            branch=row["branch"],
            priority=row["priority"],
            requires_confirmation=bool(row["requires_confirmation"]),
            result_summary=row["result_summary"],
            files_modified=json.loads(row["files_modified"]),
            errors=json.loads(row["errors"]),
            metadata=json.loads(row["metadata"]),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            id=row["id"],
            task_id=row["task_id"],
            event_type=EventType(row["event_type"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            message=row["message"],
            details=json.loads(row["details"]),
        )

    def create_task(self, task: Task) -> Task:
        task.id = task.id or str(uuid.uuid4())
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO tasks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    task.id,
                    task.user_message,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.status.value,
                    task.task_type.value,
                    task.agent_name,
                    task.repository,
                    task.branch,
                    task.priority,
                    task.requires_confirmation,
                    task.result_summary,
                    json.dumps(task.files_modified),
                    json.dumps(task.errors),
                    json.dumps(task.metadata),
                ),
            )
            conn.commit()
            self._record_event(task.id, EventType.CREATED, "Task created", conn=conn)
        return task

    def _record_event(
        self,
        task_id: str,
        event_type: EventType,
        message: str,
        details: Optional[dict] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> TaskEvent:
        event = TaskEvent(
            id=str(uuid.uuid4()),
            task_id=task_id,
            event_type=event_type,
            message=message,
            details=details or {},
        )
        target_conn = conn or self._get_db()
        with target_conn if conn is None else target_conn as c:
            c.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.task_id,
                    event.event_type.value,
                    event.timestamp.isoformat(),
                    event.message,
                    json.dumps(event.details),
                ),
            )
            if conn is None:
                c.commit()
        return event

    def update_task_status(self, task_id: str, new_status: TaskStatus) -> Task:
        with self._get_db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise TaskNotFoundError(f"Task {task_id} not found")

            task = self._row_to_task(row)
            TaskStateMachine.transition(task.status, new_status)

            task.status = new_status
            task.updated_at = datetime.utcnow()

            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (new_status.value, task.updated_at.isoformat(), task_id),
            )
            conn.commit()

            event_map = {
                TaskStatus.RUNNING: EventType.STARTED,
                TaskStatus.COMPLETED: EventType.COMPLETED,
                TaskStatus.FAILED: EventType.FAILED,
                TaskStatus.CANCELLED: EventType.CANCELLED,
                TaskStatus.BLOCKED: EventType.BLOCKED,
                TaskStatus.WAITING_CONFIRMATION: EventType.WAITING,
            }
            event_type = event_map.get(new_status, EventType.STEP_UPDATE)
            self._record_event(
                task_id, event_type, f"Status changed to {new_status.value}", conn=conn
            )
            return task

    def get_task(self, task_id: str) -> Task:
        with self._get_db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise TaskNotFoundError(f"Task {task_id} not found")
            return self._row_to_task(row)

    def list_tasks(
        self, status: Optional[TaskStatus] = None, limit: int = 50
    ) -> List[Task]:
        query = "SELECT * FROM tasks"
        params: list = []
        if status:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_db() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_task(r) for r in rows]

    def get_task_events(self, task_id: str) -> List[TaskEvent]:
        with self._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY timestamp ASC",
                (task_id,),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]
