"""Tests for the IntentRouter (T-04)."""

import pytest

from src.core.models import AgentConfig, AgentCapability, TaskType, TaskStatus
from src.orchestrator.router import (
    IntentRouter,
    IntentClassificationError,
    NoAgentAvailableError,
)
from src.orchestrator.task_manager import TaskManager


# --- Fixtures ---


@pytest.fixture
def task_manager():
    return TaskManager(":memory:")


@pytest.fixture
def codex_agent():
    return AgentConfig(
        name="codex",
        cli_command="codex",
        capabilities=[
            AgentCapability.ANALYSIS,
            AgentCapability.PLANNING,
            AgentCapability.EDITING,
            AgentCapability.TESTING,
            AgentCapability.GIT_OPS,
        ],
    )


@pytest.fixture
def analysis_only_agent():
    return AgentConfig(
        name="analyst",
        cli_command="analyst-agent",
        capabilities=[AgentCapability.ANALYSIS],
    )


@pytest.fixture
def agents(codex_agent):
    return [codex_agent]


@pytest.fixture
def router(agents, task_manager):
    return IntentRouter(agents=agents, task_manager=task_manager)


# --- classify_intent ---


class TestClassifyIntent:
    def test_analysis_keywords(self, router):
        assert router.classify_intent("analiza el codigo del modulo auth") == TaskType.ANALYSIS
        assert router.classify_intent("que hace esta funcion?") == TaskType.ANALYSIS
        assert router.classify_intent("revisa la arquitectura") == TaskType.ANALYSIS
        assert router.classify_intent("review the codebase") == TaskType.ANALYSIS

    def test_planning_keywords(self, router):
        assert router.classify_intent("planifica el login") == TaskType.PLANNING
        assert router.classify_intent("diseñar la arquitectura del sistema") == TaskType.PLANNING
        assert router.classify_intent("prepara el plan de implementacion") == TaskType.PLANNING

    def test_testing_keywords(self, router):
        assert router.classify_intent("correr los tests") == TaskType.TESTING
        assert router.classify_intent("testea el modulo de pagos") == TaskType.TESTING
        assert router.classify_intent("verificar cobertura") == TaskType.TESTING

    def test_modification_keywords(self, router):
        assert router.classify_intent("crea un endpoint para login") == TaskType.MODIFICATION
        assert router.classify_intent("implementa autenticacion OAuth") == TaskType.MODIFICATION
        assert router.classify_intent("fix the auth bug") == TaskType.MODIFICATION
        assert router.classify_intent("arregla el error en utils") == TaskType.MODIFICATION

    def test_commit_keywords(self, router):
        assert router.classify_intent("haz commit de los cambios") == TaskType.COMMIT
        assert router.classify_intent("commitea esto") == TaskType.COMMIT

    def test_push_keywords(self, router):
        assert router.classify_intent("haz push a github") == TaskType.PUSH
        assert router.classify_intent("sube al remoto") == TaskType.PUSH

    def test_pull_request_keywords(self, router):
        assert router.classify_intent("crea un pull request") == TaskType.PULL_REQUEST
        assert router.classify_intent("abre un pr") == TaskType.PULL_REQUEST
        assert router.classify_intent("mergea la rama") == TaskType.PULL_REQUEST

    def test_default_fallback(self, router):
        # Message with no recognizable keywords defaults to MODIFICATION.
        assert router.classify_intent("hola mundo") == TaskType.MODIFICATION


# --- resolve_agent ---


class TestResolveAgent:
    def test_full_capability_agent(self, router, codex_agent):
        for task_type in TaskType:
            agent = router.resolve_agent(task_type)
            assert agent.name == "codex"

    def test_no_agent_for_capability(self, task_manager, analysis_only_agent):
        r = IntentRouter(agents=[analysis_only_agent], task_manager=task_manager)
        # Should work for analysis.
        assert r.resolve_agent(TaskType.ANALYSIS).name == "analyst"
        # Should fail for git ops.
        with pytest.raises(NoAgentAvailableError):
            r.resolve_agent(TaskType.COMMIT)


# --- needs_confirmation ---


class TestNeedsConfirmation:
    @pytest.mark.parametrize(
        "task_type,expected",
        [
            (TaskType.COMMIT, True),
            (TaskType.PUSH, True),
            (TaskType.PULL_REQUEST, True),
            (TaskType.ANALYSIS, False),
            (TaskType.PLANNING, False),
            (TaskType.MODIFICATION, False),
            (TaskType.TESTING, False),
        ],
    )
    def test_confirmation_rules(self, router, task_type, expected):
        assert router.needs_confirmation(task_type) == expected


# --- route (end-to-end) ---


class TestRoute:
    def test_route_creates_task(self, router, task_manager):
        task = router.route(
            user_message="analiza el modulo de pagos",
            repository="/home/user/repo",
        )
        assert task.user_message == "analiza el modulo de pagos"
        assert task.task_type == TaskType.ANALYSIS
        assert task.agent_name == "codex"
        assert task.status == TaskStatus.PENDING
        assert task.repository == "/home/user/repo"
        assert task.requires_confirmation is False

        # Verify it was persisted.
        retrieved = task_manager.get_task(task.id)
        assert retrieved.id == task.id

    def test_route_git_ops_requires_confirmation(self, router):
        task = router.route(
            user_message="haz commit de los cambios",
            repository="/home/user/repo",
        )
        assert task.task_type == TaskType.COMMIT
        assert task.requires_confirmation is True

    def test_route_force_agent(self, router, task_manager, codex_agent):
        task = router.route(
            user_message="analiza esto",
            repository="/home/user/repo",
            force_agent="codex",
        )
        assert task.agent_name == "codex"

    def test_route_force_agent_invalid(self, router):
        with pytest.raises(ValueError, match="Agent 'nonexistent' not found"):
            router.route(
                user_message="haz algo",
                repository="/home/user/repo",
                force_agent="nonexistent",
            )

    def test_route_priority(self, router):
        task = router.route(
            user_message="arregla el bug critico",
            repository="/home/user/repo",
            priority=2,
        )
        assert task.priority == 2
        assert task.metadata["intent_confidence"] >= 0.0

    def test_route_with_branch(self, router):
        task = router.route(
            user_message="crea un feature nuevo",
            repository="/home/user/repo",
            branch="feature/new-login",
        )
        assert task.branch == "feature/new-login"


# --- intent_confidence ---


class TestIntentConfidence:
    def test_high_confidence_single_match(self, router):
        score = router._intent_confidence("analiza el codigo")
        assert score >= 0.5

    def test_low_confidence_no_match(self, router):
        score = router._intent_confidence("hola como estas")
        assert score == 0.3
