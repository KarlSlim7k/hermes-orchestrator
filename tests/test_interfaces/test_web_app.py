"""Tests para interfaces/web/app.py (T-17bis)."""

import json
import time
import urllib.request
import urllib.error

import pytest

from src.core.models import Task, TaskStatus, TaskType, AgentConfig, AgentCapability
from src.orchestrator.task_manager import TaskManager
from src.orchestrator.router import IntentRouter
from src.interfaces.web.app import WebApp, AppHandler
from src.notifications.notifier import Notifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "http://127.0.0.1"
PORT = 19001


def _http_get(path):
    url = f"{BASE}:{PORT}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _http_post(path, data=None):
    url = f"{BASE}:{PORT}{path}"
    body = json.dumps(data or {}).encode("utf-8") if data else b""
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _make_tm():
    return TaskManager(db_path=":memory:")


# ---------------------------------------------------------------------------
# Shared server fixture — one server per test module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shared_app():
    """Start one WebApp for all tests in this module."""
    tm = _make_tm()
    notifier = Notifier()
    webapp = WebApp(task_manager=tm, notifier=notifier, host="127.0.0.1", port=PORT)
    webapp.start(background=True)
    time.sleep(0.3)
    yield tm, notifier, webapp
    webapp.stop()


@pytest.fixture(autouse=True)
def fresh_db(shared_app):
    """Reset DB between tests."""
    tm, notifier, webapp = shared_app
    new_tm = _make_tm()
    # Update both webapp AND the handler class attribute
    webapp.task_manager = new_tm
    AppHandler.task_manager = new_tm
    yield new_tm


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_ok(self, shared_app):
        status, data = _http_get("/health")
        assert status == 200
        assert data["status"] == "ok"
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboardEndpoint:
    def test_dashboard_returns_html(self, shared_app):
        url = f"{BASE}:{PORT}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")
        assert "<!DOCTYPE html>" in html
        assert "Hermes Orquestador" in html
        assert resp.headers["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Tasks API — GET
# ---------------------------------------------------------------------------

class TestTasksApiGet:
    def test_list_tasks_empty(self, shared_app, fresh_db):
        status, data = _http_get("/api/tasks")
        assert status == 200
        assert data["tasks"] == []
        assert data["total"] == 0

    def test_list_tasks_with_limit(self, shared_app, fresh_db):
        status, data = _http_get("/api/tasks?limit=2")
        assert status == 200
        assert len(data["tasks"]) <= 2

    def test_get_task_detail(self, shared_app, fresh_db):
        task = Task(
            id="detail-1",
            user_message="Test task",
            task_type=TaskType.ANALYSIS,
            agent_name="opencode",
            repository="/tmp",
            status=TaskStatus.PENDING,
        )
        fresh_db.create_task(task)

        status, data = _http_get("/api/tasks/detail-1")
        assert status == 200
        assert data["id"] == "detail-1"
        assert data["user_message"] == "Test task"

    def test_get_nonexistent_task(self, shared_app, fresh_db):
        status, data = _http_get("/api/tasks/nonexistent")
        assert status == 404

    def test_get_task_events(self, shared_app, fresh_db):
        task = Task(
            id="events-1",
            user_message="Test events",
            task_type=TaskType.ANALYSIS,
            agent_name="opencode",
            repository="/tmp",
            status=TaskStatus.PENDING,
        )
        fresh_db.create_task(task)

        status, data = _http_get("/api/tasks/events-1/events")
        assert status == 200
        assert data["task_id"] == "events-1"
        assert len(data["events"]) >= 1

    def test_get_events_nonexistent_task(self, shared_app, fresh_db):
        status, data = _http_get("/api/tasks/nonexistent/events")
        assert status == 404


# ---------------------------------------------------------------------------
# Tasks API — POST
# ---------------------------------------------------------------------------

class TestTasksApiPost:
    def test_create_task(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks", {
            "user_message": "Create this task",
            "task_type": "analysis",
            "agent_name": "opencode",
            "repository": "/tmp/test",
        })
        assert status == 201
        assert data["user_message"] == "Create this task"
        assert data["status"] == "pending"

    def test_create_task_missing_message(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks", {"task_type": "analysis"})
        assert status == 400
        assert "user_message" in data["error"]

    def test_create_task_invalid_type(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks", {
            "user_message": "Test",
            "task_type": "invalid_type",
        })
        assert status == 400

    def test_start_task(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks", {
            "user_message": "To start",
            "task_type": "analysis",
            "repository": "/tmp",
        })
        task_id = data["id"]

        status, data = _http_post(f"/api/tasks/{task_id}/start")
        assert status == 200
        assert data["status"] == "running"

    def test_cancel_task(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks", {
            "user_message": "To cancel",
            "task_type": "analysis",
            "repository": "/tmp",
        })
        task_id = data["id"]

        status, data = _http_post(f"/api/tasks/{task_id}/cancel")
        assert status == 200
        assert data["status"] == "cancelled"

    def test_start_nonexistent_task(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks/nope/start")
        assert status == 404

    def test_cancel_nonexistent_task(self, shared_app, fresh_db):
        status, data = _http_post("/api/tasks/nope/cancel")
        assert status == 404


# ---------------------------------------------------------------------------
# Notifications API
# ---------------------------------------------------------------------------

class TestNotificationsApi:
    def test_notifications_empty(self, shared_app, fresh_db):
        status, data = _http_get("/api/notifications")
        assert status == 200
        assert "notifications" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# 404 handling
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_path_returns_404(self, shared_app, fresh_db):
        status, data = _http_get("/unknown/path")
        assert status == 404
        assert "error" in data

    def test_unknown_post_path_returns_404(self, shared_app, fresh_db):
        status, data = _http_post("/unknown/action", {})
        assert status == 404
        assert "error" in data


# ---------------------------------------------------------------------------
# WebApp lifecycle
# ---------------------------------------------------------------------------

class TestWebAppLifecycle:
    def test_stop_and_restart(self):
        tm = _make_tm()
        port = 19002
        webapp = WebApp(task_manager=tm, host="127.0.0.1", port=port)
        webapp.start(background=True)
        time.sleep(0.3)

        # Verify running
        url = f"{BASE}:{port}/health"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200

        webapp.stop()
        time.sleep(0.3)

        # Verify stopped — connection should be refused
        with pytest.raises(Exception):
            urllib.request.urlopen(url, timeout=2)
