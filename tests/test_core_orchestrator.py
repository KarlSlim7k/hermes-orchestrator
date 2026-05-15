import pytest
import uuid
from datetime import datetime

from src.core.models import Task, TaskType, TaskStatus, TaskEvent, EventType
from src.orchestrator.state_machine import TaskStateMachine, InvalidTransitionError
from src.orchestrator.task_manager import TaskManager, TaskNotFoundError


def test_state_machine_valid_transitions():
    assert TaskStateMachine.can_transition(TaskStatus.PENDING, TaskStatus.RUNNING)
    assert TaskStateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)
    assert TaskStateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
    assert TaskStateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.WAITING_CONFIRMATION)
    assert TaskStateMachine.can_transition(TaskStatus.WAITING_CONFIRMATION, TaskStatus.RUNNING)
    assert not TaskStateMachine.can_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    assert not TaskStateMachine.can_transition(TaskStatus.PENDING, TaskStatus.COMPLETED)


def test_state_machine_invalid_transition_raises():
    with pytest.raises(InvalidTransitionError):
        TaskStateMachine.transition(TaskStatus.PENDING, TaskStatus.COMPLETED)


def test_task_manager_create_and_get():
    tm = TaskManager()
    task = Task(
        id="test-1",
        user_message="haz un plan",
        task_type=TaskType.PLANNING,
        repository="/tmp/repo",
    )
    created = tm.create_task(task)
    assert created.id == "test-1"
    assert created.status == TaskStatus.PENDING

    fetched = tm.get_task("test-1")
    assert fetched.user_message == "haz un plan"


def test_task_manager_transition_lifecycle():
    tm = TaskManager()
    task = tm.create_task(Task(
        id="lifecycle-1",
        user_message="modifica login",
        task_type=TaskType.MODIFICATION,
        repository="/tmp/repo",
    ))

    running = tm.update_task_status(task.id, TaskStatus.RUNNING)
    assert running.status == TaskStatus.RUNNING

    completed = tm.update_task_status(task.id, TaskStatus.COMPLETED)
    assert completed.status == TaskStatus.COMPLETED


def test_task_manager_list_tasks():
    tm = TaskManager()
    tm.create_task(Task(id="a", user_message="plan", task_type=TaskType.PLANNING, repository="/r"))
    tm.create_task(Task(id="b", user_message="test", task_type=TaskType.TESTING, repository="/r"))
    tasks = tm.list_tasks(limit=2)
    assert len(tasks) == 2
    assert tasks[0].id == "b"  # latest first
