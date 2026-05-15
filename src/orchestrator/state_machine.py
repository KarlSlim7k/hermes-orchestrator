from src.core.models import TaskStatus
from typing import Dict, Set


class InvalidTransitionError(Exception):
    """Raised when a task attempts an invalid state transition."""
    pass


class TaskStateMachine:
    """Manages valid state transitions for tasks."""

    # Valid transitions map: current_state -> {allowed_next_states}
    TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
        TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.WAITING_CONFIRMATION,
            TaskStatus.CANCELLED,
        },
        TaskStatus.BLOCKED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
        TaskStatus.WAITING_CONFIRMATION: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
        TaskStatus.COMPLETED: set(),
        TaskStatus.FAILED: set(),
        TaskStatus.CANCELLED: set(),
    }

    @classmethod
    def can_transition(cls, current: TaskStatus, target: TaskStatus) -> bool:
        return target in cls.TRANSITIONS.get(current, set())

    @classmethod
    def transition(cls, current: TaskStatus, target: TaskStatus) -> TaskStatus:
        if not cls.can_transition(current, target):
            raise InvalidTransitionError(
                f"Cannot transition from {current.value} to {target.value}"
            )
        return target
