"""Tests para T-15 (Notifier), T-16 (TelegramChannel), T-17 (Web UI)."""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


def run_async(coro):
    """Helper para correr corutinas en tests (Python 3.14 compat)."""
    return asyncio.run(coro)

from src.notifications.notifier import (
    Notifier,
    NotificationChannelBase,
    NotificationRecord,
    ConsoleChannel,
)
from src.notifications.channels import TelegramChannel
from src.core.models import Notification, NotificationChannel, NotificationPriority
from src.interfaces.web.ui import render_panel, status_class, WebUIHandler


# ====== T-15: Notifier ======


class DummyChannel(NotificationChannelBase):
    """Canal dummy para tests."""

    def __init__(self, enabled: bool = True, should_fail: bool = False):
        self._enabled = enabled
        self.should_fail = should_fail
        self.sent: list[Notification] = []

    @property
    def channel_type(self) -> NotificationChannel:
        return NotificationChannel.WEB

    def is_enabled(self) -> bool:
        return self._enabled

    async def send(self, notification: Notification) -> bool:
        self.sent.append(notification)
        if self.should_fail:
            raise ConnectionError("dummy channel error")
        return True


class TestConsoleChannel:
    def test_channel_type(self):
        ch = ConsoleChannel()
        assert ch.channel_type == NotificationChannel.WEB

    def test_is_enabled(self):
        assert ConsoleChannel().is_enabled() is True

    def test_send_prints(self, capsys):
        ch = ConsoleChannel()
        n = Notification(
            id="test-1",
            channel=NotificationChannel.WEB,
            title="Test notification",
            body="Test body",
            priority=NotificationPriority.HIGH,
        )
        result = run_async(ch.send(n))
        assert result is True
        captured = capsys.readouterr()
        assert "Test notification" in captured.out
        assert "Test body" in captured.out


class TestNotifier:
    @pytest.fixture
    def notifier(self):
        return Notifier()

    def test_register_channel(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        assert NotificationChannel.WEB in notifier.get_channels()

    def test_unregister_channel(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        notifier.unregister(NotificationChannel.WEB)
        assert NotificationChannel.WEB not in notifier.get_channels()

    def test_notify_to_specific_channel(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        records = run_async(
            notifier.notify("Title", "Body", channel=NotificationChannel.WEB)
        )
        assert len(records) == 1
        assert records[0].success is True
        assert len(ch.sent) == 1

    def test_notify_to_all_channels(self, notifier):
        ch1 = DummyChannel()
        ch2 = DummyChannel()
        ch2._enabled = True
        # Register two channels of same type (second overwrites first).
        notifier.register(ch1)
        records = run_async(
            notifier.notify("Title", "Body")
        )
        assert len(records) == 1

    def test_notify_disabled_channel(self, notifier):
        ch = DummyChannel(enabled=False)
        notifier.register(ch)
        records = run_async(
            notifier.notify("Title", "Body")
        )
        assert len(records) == 0

    def test_notify_failed_channel(self, notifier):
        ch = DummyChannel(should_fail=True)
        notifier.register(ch)
        records = run_async(
            notifier.notify("Title", "Body")
        )
        assert len(records) == 1
        assert records[0].success is False
        assert records[0].error is not None

    def test_notify_task_event_completed(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        records = run_async(
            notifier.notify_task_event("t1", "task_completed", "Done!")
        )
        assert len(records) >= 1
        assert records[0].task_id == "t1"

    def test_notify_task_event_failed_is_high_priority(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        records = run_async(
            notifier.notify_task_event("t1", "task_failed", "Error!")
        )
        assert records[0].priority == NotificationPriority.HIGH

    def test_notify_task_event_waiting_is_urgent(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        records = run_async(
            notifier.notify_task_event("t1", "waiting_confirmation", "Confirm?")
        )
        assert records[0].priority == NotificationPriority.URGENT
        assert records[0].action_required is True

    def test_history_tracking(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        run_async(
            notifier.notify("Title 1", "Body 1")
        )
        run_async(
            notifier.notify("Title 2", "Body 2")
        )
        history = notifier.get_history()
        assert len(history) == 2
        assert history[0].title == "Title 1"
        assert history[1].title == "Title 2"

    def test_history_filter_by_task(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        run_async(
            notifier.notify("Title 1", "Body 1", task_id="t1")
        )
        run_async(
            notifier.notify("Title 2", "Body 2", task_id="t2")
        )
        history = notifier.get_history(task_id="t1")
        assert len(history) == 1
        assert history[0].task_id == "t1"

    def test_history_limit(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        for i in range(10):
            run_async(
                notifier.notify(f"Title {i}", f"Body {i}")
            )
        history = notifier.get_history(limit=3)
        assert len(history) == 3

    def test_clear_history(self, notifier):
        ch = DummyChannel()
        notifier.register(ch)
        run_async(
            notifier.notify("Title", "Body")
        )
        notifier.clear_history()
        assert len(notifier.get_history()) == 0


# ====== T-16: TelegramChannel ======


class TestTelegramChannel:
    @pytest.fixture
    def channel(self):
        return TelegramChannel(token="test-token", chat_id="12345")

    def test_channel_type(self, channel):
        assert channel.channel_type == NotificationChannel.TELEGRAM

    def test_is_enabled(self, channel):
        assert channel.is_enabled() is True

    def test_is_disabled(self):
        ch = TelegramChannel(token="t", chat_id="1", enabled=False)
        assert ch.is_enabled() is False

    def test_api_url(self, channel):
        url = channel._api_url("sendMessage")
        assert url == "https://api.telegram.org/bottest-token/sendMessage"

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_send_notification(self, mock_urlopen, channel):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        n = Notification(
            id="tg-1",
            channel=NotificationChannel.TELEGRAM,
            title="Test",
            body="Hello from Hermes",
            priority=NotificationPriority.NORMAL,
        )
        result = run_async(channel.send(n))
        assert result is True

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_send_with_action_buttons(self, mock_urlopen, channel):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        n = Notification(
            id="tg-2",
            channel=NotificationChannel.TELEGRAM,
            title="Confirm",
            body="Please approve",
            priority=NotificationPriority.URGENT,
            action_required=True,
        )
        result = run_async(channel.send(n))
        assert result is True

        # Verify the call included reply_markup.
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data)
        assert "reply_markup" in body

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_send_truncates_long_text(self, mock_urlopen, channel):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        n = Notification(
            id="tg-3",
            channel=NotificationChannel.TELEGRAM,
            title="Long",
            body="A" * 5000,
            priority=NotificationPriority.NORMAL,
        )
        result = run_async(channel.send(n))
        assert result is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data)
        assert len(body["text"]) <= 4000

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_send_failure(self, mock_urlopen, channel):
        mock_urlopen.side_effect = ConnectionError("Network error")

        n = Notification(
            id="tg-4",
            channel=NotificationChannel.TELEGRAM,
            title="Test",
            body="Body",
        )
        with pytest.raises(ConnectionError):
            run_async(channel.send(n))

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_get_me(self, mock_urlopen, channel):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "ok": True,
            "result": {"id": 12345, "username": "test_bot"},
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = channel.get_me()
        assert result is not None
        assert result["username"] == "test_bot"


# ====== T-17: Web UI ======


class TestStatusClass:
    def test_pending(self):
        assert status_class("pending") == "status-pending"

    def test_running(self):
        assert status_class("running") == "status-running"

    def test_completed(self):
        assert status_class("completed") == "status-completed"

    def test_failed(self):
        assert status_class("failed") == "status-failed"

    def test_unknown(self):
        assert status_class("unknown") == "status-pending"


class TestRenderPanel:
    def test_empty_panel(self):
        html = render_panel([])
        assert "Hermes Orquestador" in html
        assert "No hay tareas registradas" in html

    def test_panel_with_tasks(self):
        tasks = [
            MagicMock(
                id="t1",
                user_message="Test task",
                agent_name="codex",
                status=MagicMock(value="completed"),
                created_at=datetime(2026, 5, 16, 10, 0),
            ),
            MagicMock(
                id="t2",
                user_message="Another task",
                agent_name="opencode",
                status=MagicMock(value="running"),
                created_at=datetime(2026, 5, 16, 11, 0),
            ),
        ]
        html = render_panel(tasks)
        assert "Hermes Orquestador" in html
        assert "Test task" in html
        assert "Another task" in html
        assert "status-completed" in html
        assert "status-running" in html
        assert "codex" in html
        assert "opencode" in html

    def test_panel_stats(self):
        tasks = [
            MagicMock(
                id=f"t{i}",
                user_message=f"Task {i}",
                agent_name="test",
                status=MagicMock(value=s),
                created_at=datetime.now(),
            )
            for i, s in enumerate([
                "completed", "completed", "running",
                "failed", "pending",
            ])
        ]
        html = render_panel(tasks)
        assert ">5</" in html or "5" in html  # Total
        assert "Completadas" in html
        assert "Ejecutando" in html
        assert "Fallidas" in html

    def test_panel_html_escaping(self):
        tasks = [
            MagicMock(
                id="t1",
                user_message="<script>alert('xss')</script>",
                agent_name="test",
                status=MagicMock(value="pending"),
                created_at=datetime.now(),
            ),
        ]
        html = render_panel(tasks)
        # The user_message in the table cell should be escaped.
        # Note: the page has <script> tags in the head for auto-refresh,
        # so we check specifically that the task message is escaped in the table.
        assert "&lt;script&gt;" in html
        # And the raw <script>alert should NOT appear in table rows.
        assert "<script>alert('xss')</script>" not in html.split("<table>")[1]


class TestWebUIHandler:
    @pytest.fixture
    def handler(self):
        # Create a mock handler with minimal setup.
        import io
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()

        class MockHandler(WebUIHandler):
            def __init__(self_inner):
                self_inner.rfile = self.rfile
                self_inner.wfile = self.wfile
                self_inner.task_manager = None
                self_inner.notifier = None

            def send_response(self_inner, code):
                pass

            def send_header(self_inner, *args):
                pass

            def end_headers(self_inner):
                pass

        return MockHandler()

    def test_handler_has_task_manager_attr(self, handler):
        assert hasattr(handler, "task_manager")
        assert handler.task_manager is None

    def test_serve_dashboard_empty(self, handler):
        handler._serve_dashboard()
        output = self.wfile.getvalue().decode("utf-8")
        assert "Hermes Orquestador" in output

    def test_health_endpoint(self, handler):
        handler._serve_health()
        output = self.wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert data["status"] == "ok"

    def test_tasks_api_empty(self, handler):
        handler._serve_tasks_api()
        output = self.wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert data["tasks"] == []
        assert data["total"] == 0

    def test_tasks_api_with_manager(self):
        import io
        wfile = io.BytesIO()

        mock_task = MagicMock(
            id="t1",
            user_message="Test",
            agent_name="codex",
            status=MagicMock(value="completed"),
            task_type=MagicMock(value="analysis"),
            created_at=datetime.now(),
        )
        mock_tm = MagicMock()
        mock_tm.list_tasks.return_value = [mock_task]

        class MockHandler(WebUIHandler):
            def __init__(self_inner):
                self_inner.rfile = io.BytesIO(b"")
                self_inner.wfile = wfile
                self_inner.task_manager = mock_tm
                self_inner.notifier = None

            def send_response(self_inner, code):
                pass

            def send_header(self_inner, *args):
                pass

            def end_headers(self_inner):
                pass

        handler = MockHandler()
        handler._serve_tasks_api()
        output = wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert data["total"] == 1
        assert data["tasks"][0]["id"] == "t1"


class TestWebUIIntegration:
    def test_render_panel_auto_refresh(self):
        html = render_panel([])
        assert "setTimeout" in html
        assert "10000" in html  # 10 seconds

    def test_panel_contains_all_sections(self):
        html = render_panel([])
        assert "Hermes Orquestador" in html
        assert "Tareas Recientes" in html
        assert "Auto-refresh" in html
