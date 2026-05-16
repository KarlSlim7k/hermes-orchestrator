"""Tests de integracion para el flujo GitHub completo (T-19).

Validan el pipeline: agente → commit → push → PR
con confirmaciones en cada paso.
"""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from src.core.models import Task, TaskStatus, TaskType
from src.orchestrator.task_manager import TaskManager
from src.github.git_ops import GitOperations, GitError
from src.github.pr_manager import PRManager, PRError, PRInfo
from src.github.confirmation import (
    ConfirmationHandler,
    ConfirmationResponse,
    ConfirmAction,
)
from src.github.workflow import GitHubWorkflow, GitHubWorkflowResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def real_git_repo(tmp_path):
    """Crea un repositorio git real para testing."""
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    # Commit inicial
    readme = tmp_path / "README.md"
    readme.write_text("# hermes-orchestrator\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    return str(tmp_path)


@pytest.fixture
def components(real_git_repo):
    """Setup completo: TaskManager + GitOps + PRManager + ConfirmationHandler."""
    tm = TaskManager(db_path=":memory:")
    git_ops = GitOperations(workdir=real_git_repo)
    pr_manager = PRManager(gh_path="gh", timeout=30)
    confirm_handler = ConfirmationHandler(task_manager=tm)
    return tm, git_ops, pr_manager, confirm_handler


@pytest.fixture
def workflow(components):
    """GitHubWorkflow listo para usar."""
    tm, git_ops, pr_manager, confirm_handler = components
    return GitHubWorkflow(
        task_manager=tm,
        git_ops=git_ops,
        pr_manager=pr_manager,
        confirmation_handler=confirm_handler,
        auto_branch=True,
        base_branch="main",
    )


@pytest.fixture
def task(components):
    """Tarea de prueba persistida."""
    tm, _, _, _ = components
    task = Task(
        id="gh-task-1",
        user_message="Implementar modulo de logging",
        task_type=TaskType.MODIFICATION,
        agent_name="opencode",
        repository="/tmp/test-repo",
        branch=None,
        priority=0,
        requires_confirmation=True,
    )
    tm.create_task(task)
    return task


# ---------------------------------------------------------------------------
# T-19.1: Flujo completo con confirmaciones aprobadas
# ---------------------------------------------------------------------------

class TestGitHubWorkflowFullSuccess:
    """Flujo exitoso: commit → push → PR, todas las confirmaciones aprobadas."""

    def test_full_workflow_all_approved(self, workflow, task, components):
        tm, git_ops, pr_manager, confirm_handler = components

        # Auto-aprobar todas las confirmaciones
        confirm_handler._confirm_fn = lambda req: ConfirmationResponse(
            action=ConfirmAction.APPROVE,
        )

        # Create a file to commit so git ops succeed
        import os
        new_file = os.path.join(git_ops.workdir, "feature.py")
        with open(new_file, "w") as f:
            f.write("# new feature\n")

        # Mock push (no tenemos remoto real)
        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True, stdout="", stderr="")

            # Mock PR creation
            with patch.object(pr_manager, "create") as mock_pr:
                mock_pr.return_value = PRInfo(
                    number=42,
                    title="Implementar modulo de logging",
                    state="open",
                    url="https://github.com/user/repo/pull/42",
                    head="hermes/gh-task-1",
                    base="main",
                )

                result = workflow.execute(
                    task=task,
                    commit_message="feat: add logging module",
                    pr_title="Add logging module",
                    pr_body="Implemented logging module as requested",
                )

                # Verificar resultado
                assert result.success is True
                assert result.commit_hash is not None
                assert result.pr_number == 42
                assert result.pr_url == "https://github.com/user/repo/pull/42"
                assert result.branch is not None
                assert result.branch.startswith("hermes/")
                assert result.errors == []

                # Verificar que la rama fue creada
                assert git_ops.current_branch() == result.branch

                # Verificar que el commit existe
                log = git_ops.commit_log(count=2)
                assert any("feat: add logging module" in entry for entry in log)

    def test_workflow_without_confirmations(self, workflow, task, components):
        """Flujo sin pedir confirmaciones (confirm=False)."""
        tm, git_ops, pr_manager, confirm_handler = components

        # Create a file to commit
        import os
        new_file = os.path.join(git_ops.workdir, "mod.txt")
        with open(new_file, "w") as f:
            f.write("modified content\n")

        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True, stdout="", stderr="")

            with patch.object(pr_manager, "create") as mock_pr:
                mock_pr.return_value = PRInfo(
                    number=1,
                    title="test",
                    state="open",
                    url="https://github.com/user/repo/pull/1",
                    head="hermes/gh-task-1",
                    base="main",
                )

                result = workflow.execute(
                    task=task,
                    require_commit_confirm=False,
                    require_push_confirm=False,
                    require_pr_confirm=False,
                )

                assert result.success is True
                assert result.pr_number == 1

    def test_workflow_creates_branch_from_task_id(self, workflow, task, components):
        """La rama automatica se genera a partir del task_id."""
        tm, git_ops, pr_manager, confirm_handler = components
        confirm_handler._confirm_fn = lambda req: ConfirmationResponse(
            action=ConfirmAction.APPROVE,
        )

        # Create a file to commit
        import os
        new_file = os.path.join(git_ops.workdir, "branch_test.txt")
        with open(new_file, "w") as f:
            f.write("branch test\n")

        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True)

            with patch.object(pr_manager, "create") as mock_pr:
                mock_pr.return_value = PRInfo(
                    number=1, title="t", state="open", url="http://",
                    head="hermes/gh-task-1", base="main",
                )

                result = workflow.execute(task=task)

                assert result.branch == "hermes/gh-task-1"
                # Task object should have branch set
                assert task.branch == "hermes/gh-task-1"


# ---------------------------------------------------------------------------
# T-19.2: Confirmacion rechazada
# ---------------------------------------------------------------------------

class TestGitHubWorkflowRejected:
    """Flujo donde el usuario rechaza una confirmacion."""

    def test_commit_rejected(self, workflow, task, components):
        tm, git_ops, pr_manager, confirm_handler = components
        confirm_handler._confirm_fn = lambda req: ConfirmationResponse(
            action=ConfirmAction.REJECT,
            response_text="No quiero commitear esto",
        )

        result = workflow.execute(task=task)

        assert result.success is False
        # Verificar estado en DB
        saved = tm.get_task(task.id)
        assert saved.status == TaskStatus.CANCELLED

    def test_push_rejected(self, workflow, task, components):
        tm, git_ops, pr_manager, confirm_handler = components

        # Create a file so the commit step succeeds before push rejection
        import os
        new_file = os.path.join(git_ops.workdir, "push_reject.txt")
        with open(new_file, "w") as f:
            f.write("content\n")

        # Aprobar commit, rechazar push
        call_count = [0]
        def mixed_confirm(req):
            call_count[0] += 1
            if req.action == "commit":
                return ConfirmationResponse(action=ConfirmAction.APPROVE)
            return ConfirmationResponse(action=ConfirmAction.REJECT)

        confirm_handler._confirm_fn = mixed_confirm

        result = workflow.execute(task=task)

        assert result.success is False
        saved = tm.get_task(task.id)
        assert saved.status == TaskStatus.CANCELLED

    def test_pr_rejected(self, workflow, task, components):
        tm, git_ops, pr_manager, confirm_handler = components

        # Create a file so the commit step succeeds before PR rejection
        import os
        new_file = os.path.join(git_ops.workdir, "pr_reject.txt")
        with open(new_file, "w") as f:
            f.write("content\n")

        # Aprobar commit y push, rechazar PR
        call_count = [0]
        def mixed_confirm(req):
            call_count[0] += 1
            if req.action in ("commit", "push"):
                return ConfirmationResponse(action=ConfirmAction.APPROVE)
            return ConfirmationResponse(action=ConfirmAction.REJECT)

        confirm_handler._confirm_fn = mixed_confirm

        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True)

            result = workflow.execute(task=task)

            assert result.success is False
            saved = tm.get_task(task.id)
            assert saved.status == TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# T-19.3: Errores en operaciones git/PR
# ---------------------------------------------------------------------------

class TestGitHubWorkflowErrors:
    """Flujo con errores en operaciones git o PR."""

    def test_push_failure(self, workflow, task, components):
        tm, git_ops, pr_manager, confirm_handler = components
        confirm_handler._confirm_fn = lambda req: ConfirmationResponse(
            action=ConfirmAction.APPROVE,
        )

        # Push falla
        with patch.object(git_ops, "push") as mock_push:
            mock_push.side_effect = GitError(
                "push failed: remote not found",
                exit_code=1,
                stderr="fatal: 'origin' does not appear to be a git repository",
            )

            result = workflow.execute(task=task)

            assert result.success is False
            saved = tm.get_task(task.id)
            assert saved.status == TaskStatus.FAILED

    def test_pr_creation_failure(self, workflow, task, components):
        tm, git_ops, pr_manager, confirm_handler = components
        confirm_handler._confirm_fn = lambda req: ConfirmationResponse(
            action=ConfirmAction.APPROVE,
        )

        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True)

            # PR falla
            with patch.object(pr_manager, "create") as mock_pr:
                mock_pr.side_effect = PRError(
                    "Validation failed",
                    exit_code=1,
                    stderr="body is too long",
                )

                result = workflow.execute(task=task)

                assert result.success is False
                saved = tm.get_task(task_id := task.id)
                assert saved.status == TaskStatus.FAILED
                assert len(result.errors) > 0

    def test_branch_creation_failure(self, workflow, components):
        """Si no se puede crear la rama, el flujo falla inmediatamente."""
        tm, git_ops, pr_manager, confirm_handler = components
        workflow2 = GitHubWorkflow(
            task_manager=tm,
            git_ops=git_ops,
            pr_manager=pr_manager,
            confirmation_handler=confirm_handler,
            auto_branch=True,
        )

        task = Task(
            id="branch-fail-1",
            user_message="test",
            task_type=TaskType.MODIFICATION,
            agent_name="opencode",
            repository="/tmp",
            branch=None,
        )
        tm.create_task(task)

        # Intentar crear rama con nombre invalido (caracteres especiales)
        with patch.object(git_ops, "create_and_checkout") as mock_branch:
            mock_branch.side_effect = GitError(
                "invalid branch name",
                exit_code=1,
                stderr="fatal: Invalid branch name",
            )

            result = workflow2.execute(task=task)

            assert result.success is False
            assert any("branch" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# T-19.4: Integracion con el router y task manager
# ---------------------------------------------------------------------------

class TestGitHubWorkflowRouterIntegration:
    """Integracion del flujo GitHub con el router de intencion."""

    def test_router_creates_git_task_then_workflow(self):
        """Router crea tarea de tipo COMMIT → workflow la ejecuta."""
        from src.orchestrator.router import IntentRouter
        from src.core.models import AgentConfig, AgentCapability

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

        # Paso 1: Router clasifica un mensaje de commit
        task = router.route(
            user_message="Commitea los cambios del modulo",
            repository="/tmp/test",
        )

        assert task.task_type == TaskType.COMMIT
        assert task.requires_confirmation is True

        # Paso 2: Verificar que la tarea persistio
        saved = tm.get_task(task.id)
        assert saved.status == TaskStatus.PENDING
        assert saved.task_type == TaskType.COMMIT

    def test_router_pr_task_with_confirmation(self):
        """Router clasifica PR y marca requiere_confirmacion."""
        from src.orchestrator.router import IntentRouter
        from src.core.models import AgentConfig, AgentCapability

        tm = TaskManager(db_path=":memory:")
        agents = [
            AgentConfig(
                name="opencode",
                cli_command="opencode",
                capabilities=[AgentCapability.GIT_OPS],
                timeout_seconds=600,
            ),
        ]
        router = IntentRouter(agents=agents, task_manager=tm)

        task = router.route(
            user_message="Crea un PR a main con los cambios",
            repository="/tmp/test",
        )

        assert task.task_type == TaskType.PULL_REQUEST
        assert task.requires_confirmation is True


# ---------------------------------------------------------------------------
# T-19.5: Flujo con confirmacion via callback (Telegram buttons)
# ---------------------------------------------------------------------------

class TestGitHubWorkflowCallbackConfirmation:
    """Confirmacion via callback externo (simulando botones de Telegram)."""

    def test_workflow_with_async_approval(self, workflow, task, components):
        """Simular aprobacion asincrona via callback."""
        tm, git_ops, pr_manager, confirm_handler = components

        # Simular que la confirmacion viene via callback (ej. boton de Telegram)
        confirm_handler._confirm_fn = lambda req: ConfirmationResponse(
            action=ConfirmAction.APPROVE,
        )

        # Create a file to commit
        import os
        new_file = os.path.join(git_ops.workdir, "callback_test.txt")
        with open(new_file, "w") as f:
            f.write("callback test content\n")

        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True)

            with patch.object(pr_manager, "create") as mock_pr:
                mock_pr.return_value = PRInfo(
                    number=99, title="test", state="open",
                    url="https://github.com/user/repo/pull/99",
                    head="hermes/gh-task-1", base="main",
                )

                result = workflow.execute(task=task)

                assert result.success is True
                assert result.pr_number == 99

    def test_workflow_with_programmatic_approve(self, workflow, task, components):
        """Aprobacion programatica directa."""
        tm, git_ops, pr_manager, confirm_handler = components

        # Create a file to commit
        import os
        new_file = os.path.join(git_ops.workdir, "prog_approve.txt")
        with open(new_file, "w") as f:
            f.write("programmatic approve\n")

        with patch.object(git_ops, "push") as mock_push:
            mock_push.return_value = MagicMock(success=True)

            with patch.object(pr_manager, "create") as mock_pr:
                mock_pr.return_value = PRInfo(
                    number=5, title="t", state="open",
                    url="http://",
                    head="hermes/gh-task-1", base="main",
                )

                # Aprobar via confirm_handler directo
                confirm_handler._confirm_fn = lambda req: confirm_handler.approve(req.task_id)

                result = workflow.execute(task=task)

                assert result.success is True
