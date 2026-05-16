"""Tests de integracion end-to-end (T-18).

Validan el flujo completo:
  mensaje → clasificacion → creacion de tarea → ejecucion → notificacion
"""

import asyncio
import time
import urllib.request
import urllib.error
import json
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

import pytest

from src.core.models import (
    Task, TaskStatus, TaskType,
    AgentConfig, AgentCapability,
)
from src.core.config import load_config
from src.core.logging import setup_logging, get_logger
from src.orchestrator.task_manager import TaskManager
from src.orchestrator.router import IntentRouter, NoAgentAvailableError
from src.orchestrator.state_machine import TaskStateMachine
from src.interfaces.telegram import TelegramBot, TaskExecutor
from src.interfaces.web.app import WebApp, AppHandler
from src.notifications.notifier import Notifier, ConsoleChannel
from src.agents.base import BaseAgent, AgentResult
from src.main import HermesOrchestrator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WEB_PORT = 19100


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging between tests."""
    root = get_logger("").parent
    root.handlers.clear()


@pytest.fixture
def full_setup():
    """Setup completo con todos los componentes."""
    setup_logging(level="INFO", log_file=None)

    tm = TaskManager(db_path=":memory:")
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
    router = IntentRouter(agents=agents, task_manager=tm)
    notifier = Notifier()
    notifier.register(ConsoleChannel())

    return tm, router, notifier


# ---------------------------------------------------------------------------
# T-18.1: Flujo completo de enrutamiento
# ---------------------------------------------------------------------------

class TestEndToEndRouting:
    """Prueba que el flujo message → router → task funciona."""

    def test_full_routing_pipeline(self, full_setup):
        tm, router, notifier = full_setup

        # Simular mensaje del usuario
        message = "Analiza el codigo en src/core/"

        # Router clasifica y crea tarea
        task = router.route(
            user_message=message,
            repository="/tmp/test-repo",
        )

        # Verificar que la tarea fue creada correctamente
        assert task.id is not None
        assert task.user_message == message
        assert task.status == TaskStatus.PENDING
        assert task.task_type == TaskType.ANALYSIS
        assert task.agent_name == "opencode"
        assert task.repository == "/tmp/test-repo"

        # Verificar que la tarea persistio
        saved = tm.get_task(task.id)
        assert saved.id == task.id
        assert saved.status == TaskStatus.PENDING

        # Verificar eventos
        events = tm.get_task_events(task.id)
        assert len(events) >= 1
        assert events[0].event_type.value == "task_created"

    def test_routing_git_task_requires_confirmation(self, full_setup):
        tm, router, notifier = full_setup
        task = router.route(
            user_message="Commitea los cambios",
            repository="/tmp/test-repo",
        )
        assert task.task_type == TaskType.COMMIT
        assert task.requires_confirmation is True

    def test_routing_editing_task(self, full_setup):
        tm, router, notifier = full_setup
        task = router.route(
            user_message="Crea un modulo de logging",
            repository="/tmp/test-repo",
        )
        assert task.task_type == TaskType.MODIFICATION
        assert task.requires_confirmation is False

    def test_routing_no_agent_raises(self):
        """Cuando no hay agente con la capacidad necesaria."""
        tm = TaskManager(db_path=":memory:")
        agents = [
            AgentConfig(
                name="limited",
                cli_command="limited",
                capabilities=[AgentCapability.ANALYSIS],
                timeout_seconds=600,
            ),
        ]
        router = IntentRouter(agents=agents, task_manager=tm)

        # Git ops requiere GIT_OPS, que no tiene el agente
        with pytest.raises(NoAgentAvailableError):
            router.route(
                user_message="Commitea los cambios",
                repository="/tmp/test-repo",
            )


# ---------------------------------------------------------------------------
# T-18.2: Flujo completo de ejecucion
# ---------------------------------------------------------------------------

class FakeAgent(BaseAgent):
    """Agente fake para integracion."""

    def __init__(self, result: AgentResult, **kwargs):
        super().__init__(cli_command="fake", **kwargs)
        self._result = result
        self.calls = []

    def build_command(self, prompt, workdir=None, extra_args=None):
        self.calls.append(("build_command", prompt, workdir))
        return ["fake", prompt]

    def parse_result(self, stdout, stderr, exit_code):
        return self._result

    def run_sync(self, prompt, workdir=None, extra_args=None):
        self.calls.append(("run_sync", prompt, workdir))
        return self._result


class TestEndToEndExecution:
    """Prueba el flujo completo: mensaje → ruta → ejecucion → resultado."""

    @pytest.mark.asyncio
    async def test_full_execution_success(self):
        tm = TaskManager(db_path=":memory:")
        agents = [
            AgentConfig(
                name="opencode",
                cli_command="opencode",
                capabilities=[
                    AgentCapability.ANALYSIS,
                    AgentCapability.EDITING,
                    AgentCapability.GIT_OPS,
                ],
                timeout_seconds=600,
            ),
        ]
        router = IntentRouter(agents=agents, task_manager=tm)
        notifier = Notifier()
        notifier.register(ConsoleChannel())

        agent = FakeAgent(AgentResult(
            status="completed",
            summary="Analisis completado: 3 archivos encontrados",
            files_modified=["src/core/utils.py"],
        ))
        executor = TaskExecutor(
            task_manager=tm,
            agent_registry={"opencode": agent},
            notifier=notifier,
        )

        # Paso 1: Enrutar mensaje
        task = router.route(
            user_message="Analiza el codigo",
            repository="/tmp/test",
        )
        assert task.status == TaskStatus.PENDING

        # Paso 2: Ejecutar
        result_task = await executor.execute(task)

        # Paso 3: Verificar resultado
        assert result_task.status == TaskStatus.COMPLETED
        assert "Analisis completado" in (result_task.result_summary or "")
        assert len(agent.calls) >= 1
        assert agent.calls[0][0] == "run_sync"

        # Paso 4: Verificar eventos en DB
        events = tm.get_task_events(task.id)
        event_types = [e.event_type.value for e in events]
        assert "task_created" in event_types
        assert "task_started" in event_types
        assert "task_completed" in event_types

    @pytest.mark.asyncio
    async def test_full_execution_failure(self):
        tm = TaskManager(db_path=":memory:")
        agents = [
            AgentConfig(
                name="opencode",
                cli_command="opencode",
                capabilities=[AgentCapability.ANALYSIS],
                timeout_seconds=600,
            ),
        ]
        router = IntentRouter(agents=agents, task_manager=tm)
        notifier = Notifier()
        notifier.register(ConsoleChannel())

        agent = FakeAgent(AgentResult(
            status="failed",
            summary="Error: archivo no encontrado",
            errors=["FileNotFoundError: src/core/missing.py"],
            exit_code=1,
        ))
        executor = TaskExecutor(
            task_manager=tm,
            agent_registry={"opencode": agent},
            notifier=notifier,
        )

        task = router.route(
            user_message="Analiza el codigo",
            repository="/tmp/test",
        )
        result_task = await executor.execute(task)

        assert result_task.status == TaskStatus.FAILED
        events = tm.get_task_events(task.id)
        event_types = [e.event_type.value for e in events]
        assert "task_failed" in event_types


# ---------------------------------------------------------------------------
# T-18.3: Flujo completo con interfaz web
# ---------------------------------------------------------------------------

class TestEndToEndWebIntegration:
    """Prueba el flujo completo via la API web."""

    @pytest.fixture(autouse=True)
    def setup_web(self):
        tm = TaskManager(db_path=":memory:")
        agents = [
            AgentConfig(
                name="opencode",
                cli_command="opencode",
                capabilities=[
                    AgentCapability.ANALYSIS,
                    AgentCapability.EDITING,
                    AgentCapability.GIT_OPS,
                ],
                timeout_seconds=600,
            ),
        ]
        router = IntentRouter(agents=agents, task_manager=tm)
        notifier = Notifier()
        notifier.register(ConsoleChannel())

        agent = FakeAgent(AgentResult(
            status="completed",
            summary="Task done",
            files_modified=["file.py"],
        ))
        executor = TaskExecutor(
            task_manager=tm,
            agent_registry={"opencode": agent},
            notifier=notifier,
        )

        webapp = WebApp(
            task_manager=tm,
            notifier=notifier,
            host="127.0.0.1",
            port=WEB_PORT,
        )
        webapp.start(background=True)
        time.sleep(0.3)

        self.tm = tm
        self.router = router
        self.executor = executor
        self.notifier = notifier
        self.webapp = webapp
        yield
        webapp.stop()

    def _http_post(self, path, data=None):
        url = f"http://127.0.0.1:{WEB_PORT}{path}"
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

    def _http_get(self, path):
        url = f"http://127.0.0.1:{WEB_PORT}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def test_api_to_execution_pipeline(self):
        """Crear tarea via API → ejecutar → verificar resultado via API."""
        # 1. Crear tarea via API
        status, data = self._http_post("/api/tasks", {
            "user_message": "Analiza el codigo",
            "task_type": "analysis",
            "agent_name": "opencode",
            "repository": "/tmp/test",
        })
        assert status == 201
        task_id = data["id"]

        # 2. Iniciar tarea via API
        status, data = self._http_post(f"/api/tasks/{task_id}/start")
        assert status == 200
        assert data["status"] == "running"

        # 3. Verificar via API que la tarea existe
        status, data = self._http_get(f"/api/tasks/{task_id}")
        assert status == 200
        assert data["id"] == task_id
        assert data["status"] == "running"

        # 4. Verificar eventos via API
        status, data = self._http_get(f"/api/tasks/{task_id}/events")
        assert status == 200
        assert len(data["events"]) >= 2  # created + started


# ---------------------------------------------------------------------------
# T-18.4: Orchestrator integration
# ---------------------------------------------------------------------------

class TestHermesOrchestratorIntegration:
    """Prueba que el Orchestrator conecta todos los componentes."""

    def test_orchestrator_setup(self):
        config = load_config(config_path=None, env_overrides=False)
        orch = HermesOrchestrator(config=config, db_path=":memory:")
        orch.setup(register_default_agent=False)  # No real agents in test

        assert orch.task_manager is not None
        assert orch.notifier is not None
        assert orch.router is not None
        assert orch.executor is not None

    def test_orchestrator_full_setup_with_fake_agent(self):
        config = load_config(config_path=None, env_overrides=False)
        orch = HermesOrchestrator(config=config, db_path=":memory:")

        # Override agent building to use fake
        orch._build_agent = lambda cfg: FakeAgent(AgentResult(
            status="completed", summary="ok"
        ))
        orch.setup(register_default_agent=True)

        assert "opencode" in orch.agent_registry
        assert orch.router is not None
        assert orch.executor is not None

    def test_orchestrator_web_start(self):
        config = load_config(config_path=None, env_overrides=False)
        orch = HermesOrchestrator(config=config, db_path=":memory:")
        orch.setup(register_default_agent=False)

        orch.start_web(host="127.0.0.1", port=19101)
        time.sleep(0.3)

        try:
            with urllib.request.urlopen("http://127.0.0.1:19101/health", timeout=5) as resp:
                assert resp.status == 200
        finally:
            orch.stop()
