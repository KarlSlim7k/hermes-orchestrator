from enum import Enum
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    ANALYSIS = "analysis"
    PLANNING = "planning"
    MODIFICATION = "modification"
    TESTING = "testing"
    COMMIT = "commit"
    PUSH = "push"
    PULL_REQUEST = "pull_request"


class Task(BaseModel):
    id: str = Field(..., description="Unique task ID (UUID or short hash)")
    user_message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: TaskStatus = TaskStatus.PENDING
    task_type: TaskType
    agent_name: Optional[str] = None
    repository: str
    branch: Optional[str] = None
    priority: int = 0
    requires_confirmation: bool = True
    result_summary: Optional[str] = None
    files_modified: List[str] = []
    errors: List[str] = []
    metadata: Dict[str, Any] = {}

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if v not in (0, 1, 2):
            raise ValueError("Priority must be 0 (normal), 1 (high), or 2 (urgent)")
        return v


class EventType(str, Enum):
    CREATED = "task_created"
    STARTED = "task_started"
    STEP_UPDATE = "step_completed"
    FILE_CHANGED = "file_changed"
    TESTS_RUN = "tests_run"
    WAITING = "waiting_confirmation"
    BLOCKED = "task_blocked"
    COMPLETED = "task_completed"
    FAILED = "task_failed"
    CANCELLED = "task_cancelled"


class TaskEvent(BaseModel):
    id: str
    task_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    message: str
    details: Dict[str, Any] = {}


class AgentCapability(str, Enum):
    ANALYSIS = "analysis"
    PLANNING = "planning"
    EDITING = "editing"
    TESTING = "testing"
    GIT_OPS = "git_ops"


class AgentConfig(BaseModel):
    name: str
    cli_command: str
    capabilities: List[AgentCapability]
    supports_progress: bool = False
    progress_pattern: Optional[str] = None
    timeout_seconds: int = 600
    workdir: Optional[str] = None


class NotificationChannel(str, Enum):
    TELEGRAM = "telegram"
    WEB = "web"
    EMAIL = "email"


class NotificationPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Notification(BaseModel):
    id: str
    channel: NotificationChannel
    task_id: Optional[str] = None
    priority: NotificationPriority = NotificationPriority.NORMAL
    title: str
    body: str
    action_required: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SecurityPolicy(BaseModel):
    require_confirmation_for_commit: bool = True
    require_confirmation_for_push: bool = True
    require_confirmation_for_pr: bool = True
    max_concurrent_tasks: int = 3


class ChannelConfig(BaseModel):
    telegram_enabled: bool = False
    telegram_token: Optional[str] = None
    web_enabled: bool = False
    web_port: int = 8000


class SystemConfig(BaseModel):
    agents: List[AgentConfig]
    default_agent: str
    repository_path: str
    security: SecurityPolicy
    channels: ChannelConfig
