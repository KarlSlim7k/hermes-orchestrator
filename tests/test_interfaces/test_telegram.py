"""Tests para interfaces/telegram.py (T-16bis)."""

import asyncio
import json
from typing import Optional
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.core.models import (
    Task, TaskStatus, TaskType, AgentConfig, AgentCapability,
)
from src.orchestrator.router import IntentRouter
from src.orchestrator.task_manager import TaskManager
from src.notifications.notifier import Notifier
from src.agents.base import BaseAgent, AgentResult


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_task_manager() -> TaskManager:
    return TaskManager(db_path=":memory:")


def _make_router(task_manager: TaskManager) -> IntentRouter:
    agents = [
        AgentConfig(
            name="opencode",
            cli_command="opencode",
            capabilities=[
                AgentCapability.ANALYSIS,
                AgentCapability.PLANNING,
                AgentCapability.EDITING,
                AgentCapability.TESTING,
                AgentCapability.GIT_OPS,
            ],
            supports_progress=True,
            timeout_seconds=600,
        ),
    ]
    return IntentRouter(agents=agents, task_manager=task_manager)


def _make_notifier() -> Notifier:
    return Notifier()


class FakeAgent(BaseAgent):
    """Agente fake para testing que retorna resultados predefinidos."""

    def __init__(self, result: AgentResult, **kwargs):
        super().__init__(cli_command="fake", **kwargs)
        self._result = result
        self.last_prompt: Optional[str] = None

    def build_command(self, prompt, workdir=None, extra_args=None):
        return ["fake", prompt]

    def parse_result(self, stdout, stderr, exit_code):
        return self._result

    def run_sync(self, prompt, workdir=None, extra_args=None):
        """Override para evitar ejecutar subprocess real."""
        self.last_prompt = prompt
        return self._result


def _make_executor(task_manager, result_status="completed"):
    from src.interfaces.telegram import TaskExecutor
    notifier = _make_notifier()
    agent = FakeAgent(AgentResult(
        status=result_status,
        summary="Tarea completada exitosamente",
        files_modified=["src/main.py"],
    ))
    return TaskExecutor(
        task_manager=task_manager,
        agent_registry={"opencode": agent},
        notifier=notifier,
    )


@pytest.fixture
def setup_components():
    """Fixture que retorna (task_manager, router, executor, notifier)."""
    tm = _make_task_manager()
    router = _make_router(tm)
    executor = _make_executor(tm)
    notifier = _make_notifier()
    return tm, router, executor, notifier


# ---------------------------------------------------------------------------
# TaskExecutor tests
# ---------------------------------------------------------------------------

class TestTaskExecutor:
    @pytest.mark.asyncio
    async def test_execute_success(self, setup_components):
        tm, router, executor, notifier = setup_components
        task = router.route(
            user_message="Analiza el codigo",
            repository="/tmp/test",
        )
        result = await executor.execute(task)
        assert result.status == TaskStatus.COMPLETED
        assert result.result_summary is not None

    @pytest.mark.asyncio
    async def test_execute_missing_agent(self, setup_components):
        tm, router, executor, notifier = setup_components
        task = router.route(
            user_message="Analiza el codigo",
            repository="/tmp/test",
        )
        # Quitar el agente del registry
        executor.agent_registry = {}
        result = await executor.execute(task)
        assert result.status == TaskStatus.FAILED


# ---------------------------------------------------------------------------
# TelegramBot message handling tests
# ---------------------------------------------------------------------------

class TestTelegramBotMessageHandling:
    def _make_bot(self, setup_components):
        from src.interfaces.telegram import TelegramBot
        tm, router, executor, notifier = setup_components
        return TelegramBot(
            token="fake-token",
            router=router,
            executor=executor,
            notifier=notifier,
            chat_id="12345",
            default_repository="/tmp/test",
        )

    @pytest.mark.asyncio
    async def test_handle_help_command(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send:
            await bot.handle_message("12345", "/help", 1)
            assert mock_send.called
            text = mock_send.call_args[0][1]
            assert "Hermes Orquestador" in text

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send:
            await bot.handle_message("12345", "/unknown", 1)
            text = mock_send.call_args[0][1]
            assert "desconocido" in text

    @pytest.mark.asyncio
    async def test_handle_status_no_tasks(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send:
            await bot.handle_message("12345", "/status", 1)
            text = mock_send.call_args[0][1]
            assert "No hay tareas" in text

    @pytest.mark.asyncio
    async def test_handle_tasks_no_tasks(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send:
            await bot.handle_message("12345", "/tasks", 1)
            text = mock_send.call_args[0][1]
            assert "No hay tareas" in text

    @pytest.mark.asyncio
    async def test_handle_cancel_no_running(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send:
            await bot.handle_message("12345", "/cancel", 1)
            text = mock_send.call_args[0][1]
            assert "No hay tareas en ejecucion" in text

    @pytest.mark.asyncio
    async def test_handle_message_routes_task(self, setup_components):
        """Un mensaje normal debe crear una tarea via el router."""
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send:
            with patch.object(bot, "send_message_with_buttons"):
                await bot.handle_message("12345", "Analiza el codigo en src/", 1)
                assert mock_send.called
                text = mock_send.call_args[0][1]
                assert "Tarea recibida" in text or "Ejecutando" in text

    @pytest.mark.asyncio
    async def test_handle_git_message_requires_confirmation(self, setup_components):
        """Los mensajes de commit/push/PR requieren confirmacion."""
        bot = self._make_bot(setup_components)
        with patch.object(bot, "send_message") as mock_send_msg:
            with patch.object(bot, "send_message_with_buttons") as mock_btn:
                await bot.handle_message("12345", "commitea los cambios", 1)
                # Debe enviar botones de confirmacion
                assert mock_btn.called
                buttons = mock_btn.call_args[0][2]
                # Botones deben incluir Ejecutar y Cancelar (con emoji)
                button_texts = [b["text"] for row in buttons for b in row]
                assert any("Ejecutar" in t for t in button_texts)
                assert any("Cancelar" in t for t in button_texts)

    @pytest.mark.asyncio
    async def test_chat_id_filter(self, setup_components):
        """Mensajes de chats no permitidos deben ignorarse."""
        bot = self._make_bot(setup_components)
        assert bot._is_allowed_chat("12345") is True
        assert bot._is_allowed_chat("99999") is False

    @pytest.mark.asyncio
    async def test_no_chat_id_filter_accepts_all(self, setup_components):
        """Sin chat_id configurado, acepta todos los chats."""
        tm, router, executor, notifier = setup_components
        from src.interfaces.telegram import TelegramBot
        bot = TelegramBot(
            token="fake-token",
            router=router,
            executor=executor,
            notifier=notifier,
            chat_id=None,  # Sin filtro
        )
        assert bot._is_allowed_chat("any_chat_id") is True


# ---------------------------------------------------------------------------
# TelegramBot callback handling tests
# ---------------------------------------------------------------------------

class TestTelegramBotCallbacks:
    def _make_bot(self, setup_components):
        from src.interfaces.telegram import TelegramBot
        tm, router, executor, notifier = setup_components
        return TelegramBot(
            token="fake-token",
            router=router,
            executor=executor,
            notifier=notifier,
            chat_id="12345",
            default_repository="/tmp/test",
        )

    @pytest.mark.asyncio
    async def test_handle_callback_cancel(self, setup_components):
        bot = self._make_bot(setup_components)
        # Crear tarea running
        tm, router, _, _ = setup_components
        task = router.route(
            user_message="Analiza el codigo",
            repository="/tmp/test",
        )
        tm.update_task_status(str(task.id), TaskStatus.RUNNING)

        with patch.object(bot, "answer_callback") as mock_answer:
            with patch.object(bot, "send_message") as mock_send:
                await bot.handle_callback({
                    "id": "cb-1",
                    "data": f"cancel:{task.id}",
                    "message": {"chat": {"id": "12345"}},
                })
                assert mock_answer.called
                assert mock_send.called
                text = mock_send.call_args[0][1]
                assert "cancelada" in text.lower()

    @pytest.mark.asyncio
    async def test_handle_callback_invalid_format(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "answer_callback") as mock_answer:
            await bot.handle_callback({
                "id": "cb-1",
                "data": "invalid-no-colon",
                "message": {"chat": {"id": "12345"}},
            })
            text = mock_answer.call_args[0][1]
            assert "invalido" in text.lower()


# ---------------------------------------------------------------------------
# TelegramBot send methods tests
# ---------------------------------------------------------------------------

class TestTelegramBotSendMethods:
    def _make_bot(self, setup_components):
        from src.interfaces.telegram import TelegramBot
        tm, router, executor, notifier = setup_components
        return TelegramBot(
            token="fake-token",
            router=router,
            executor=executor,
            notifier=notifier,
            chat_id="12345",
        )

    def test_send_message_truncates_long_text(self, setup_components):
        bot = self._make_bot(setup_components)
        long_text = "x" * 5000
        with patch.object(bot, "_post", return_value={"ok": True}) as mock_post:
            bot.send_message("12345", long_text)
            sent_text = mock_post.call_args[0][1]["text"]
            assert len(sent_text) <= 4000
            assert sent_text.endswith("...")

    def test_send_message_returns_false_on_failure(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "_post", return_value=None):
            result = bot.send_message("12345", "test")
            assert result is False

    def test_send_message_with_buttons(self, setup_components):
        bot = self._make_bot(setup_components)
        buttons = [[{"text": "OK", "callback_data": "ok:1"}]]
        with patch.object(bot, "_post", return_value={"ok": True}) as mock_post:
            bot.send_message_with_buttons("12345", "Choose:", buttons)
            markup = json.loads(mock_post.call_args[0][1]["reply_markup"])
            assert "inline_keyboard" in markup


# ---------------------------------------------------------------------------
# Polling tests
# ---------------------------------------------------------------------------

class TestTelegramBotPolling:
    def _make_bot(self, setup_components):
        from src.interfaces.telegram import TelegramBot
        tm, router, executor, notifier = setup_components
        return TelegramBot(
            token="fake-token",
            router=router,
            executor=executor,
            notifier=notifier,
            chat_id="12345",
        )

    def test_get_updates_parses_response(self, setup_components):
        bot = self._make_bot(setup_components)
        mock_response = {
            "ok": True,
            "result": [
                {"update_id": 100, "message": {"chat": {"id": "12345"}, "text": "hello", "message_id": 1}},
            ],
        }
        with patch.object(bot, "_get", return_value=mock_response):
            updates = bot.get_updates()
            assert len(updates) == 1
            assert updates[0]["update_id"] == 100
            assert bot._last_update_id == 100

    def test_get_updates_empty(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "_get", return_value={"ok": True, "result": []}):
            updates = bot.get_updates()
            assert updates == []

    def test_get_updates_handles_error(self, setup_components):
        bot = self._make_bot(setup_components)
        with patch.object(bot, "_get", return_value=None):
            updates = bot.get_updates()
            assert updates == []

    @pytest.mark.asyncio
    async def test_poll_once_processes_message(self, setup_components):
        bot = self._make_bot(setup_components)
        mock_updates = [
            {
                "update_id": 100,
                "message": {
                    "chat": {"id": "12345"},
                    "text": "/help",
                    "message_id": 1,
                },
            },
        ]
        with patch.object(bot, "get_updates", return_value=mock_updates):
            with patch.object(bot, "handle_message") as mock_handle:
                await bot.poll_once()
                mock_handle.assert_called_once_with("12345", "/help", 1)

    @pytest.mark.asyncio
    async def test_poll_once_processes_callback(self, setup_components):
        bot = self._make_bot(setup_components)
        mock_updates = [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb-1",
                    "data": "exec:task-1",
                    "message": {"chat": {"id": "12345"}},
                },
            },
        ]
        with patch.object(bot, "get_updates", return_value=mock_updates):
            with patch.object(bot, "handle_callback") as mock_handle:
                await bot.poll_once()
                mock_handle.assert_called_once_with(mock_updates[0]["callback_query"])

    def test_stop_sets_running_false(self, setup_components):
        bot = self._make_bot(setup_components)
        bot._running = True
        bot.stop()
        assert bot._running is False
