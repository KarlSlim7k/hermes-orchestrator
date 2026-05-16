"""Flujo GitHub completo con confirmacion (T-19).

Orquesta el pipeline: agente → commit → push → PR
con confirmaciones integradas en cada paso.
"""

from typing import Optional, Callable
from dataclasses import dataclass, field

from src.core.models import Task, TaskStatus, TaskType
from src.core.logging import get_logger
from src.orchestrator.task_manager import TaskManager
from src.github.git_ops import GitOperations, GitError
from src.github.pr_manager import PRManager, PRError
from src.github.confirmation import (
    ConfirmationHandler,
    ConfirmationResponse,
    ConfirmAction,
)

logger = get_logger("github_workflow")


@dataclass
class GitHubWorkflowResult:
    """Resultado del flujo GitHub completo."""
    success: bool
    task: Task
    commit_hash: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    branch: Optional[str] = None
    errors: list[str] = field(default_factory=list)


class GitHubWorkflow:
    """Orquesta el flujo completo: commit → push → PR.

    Cada paso requiere confirmacion si esta habilitado en la politica.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        git_ops: GitOperations,
        pr_manager: PRManager,
        confirmation_handler: ConfirmationHandler,
        auto_branch: bool = True,
        base_branch: str = "main",
    ):
        """
        Args:
            task_manager: TaskManager para actualizar estados.
            git_ops: GitOperations para operaciones git.
            pr_manager: PRManager para crear PRs.
            confirmation_handler: ConfirmationHandler para aprobaciones.
            auto_branch: Si True, crea rama automatica basada en task_id.
            base_branch: Rama base para el PR.
        """
        self.task_manager = task_manager
        self.git_ops = git_ops
        self.pr_manager = pr_manager
        self.confirmation_handler = confirmation_handler
        self.auto_branch = auto_branch
        self.base_branch = base_branch

    def execute(
        self,
        task: Task,
        commit_message: Optional[str] = None,
        pr_title: Optional[str] = None,
        pr_body: Optional[str] = None,
        require_commit_confirm: bool = True,
        require_push_confirm: bool = True,
        require_pr_confirm: bool = True,
    ) -> GitHubWorkflowResult:
        """Ejecutar el flujo completo de commit → push → PR.

        Args:
            task: Tarea a ejecutar.
            commit_message: Mensaje de commit. Si None, usa user_message.
            pr_title: Titulo del PR. Si None, usa commit_message.
            pr_body: Cuerpo del PR. Si None, usa user_message.
            require_commit_confirm: Si True, pide confirmacion para commit.
            require_push_confirm: Si True, pide confirmacion para push.
            require_pr_confirm: Si True, pide confirmacion para PR.

        Returns:
            GitHubWorkflowResult con resultado y metadatos.
        """
        task_id = task.id
        result = GitHubWorkflowResult(success=False, task=task)

        # PENDING → RUNNING
        try:
            self.task_manager.update_task_status(task_id, TaskStatus.RUNNING)
        except Exception as e:
            result.errors.append(f"Failed to start task: {e}")
            return result

        # -- Paso 1: Crear rama --
        if self.auto_branch:
            branch_name = f"hermes/{task_id}"
            try:
                self.git_ops.create_and_checkout(branch_name)
                result.branch = branch_name
                task.branch = branch_name
                logger.info(f"Created branch: {branch_name}")
            except GitError as e:
                result.errors.append(f"Failed to create branch: {e}")
                self._fail_task(task_id, result)
                return result

        # -- Paso 2: Commit --
        message = commit_message or task.user_message or f"hermes: task {task_id}"
        # Reload task from DB to get current status (RUNNING after step 1)
        task = self.task_manager.get_task(task_id)
        commit_result = self.confirmation_handler.commit_with_confirmation(
            task=task,
            git_ops=self.git_ops,
            message=message,
            confirm=require_commit_confirm,
            update_status=False,
        )
        if commit_result.status in (TaskStatus.CANCELLED, TaskStatus.FAILED):
            result.errors.extend(commit_result.errors)
            if commit_result.result_summary:
                result.errors.append(commit_result.result_summary)
            self._set_task_status(task_id, commit_result.status)
            return result

        # Obtener hash del commit
        try:
            result.commit_hash = self.git_ops.latest_commit_hash()
        except GitError:
            pass  # No fatal

        # -- Paso 3: Push --
        branch = task.branch or result.branch
        # Reload task from DB for correct state
        task = self.task_manager.get_task(task_id)
        push_result = self.confirmation_handler.push_with_confirmation(
            task=task,
            git_ops=self.git_ops,
            branch=branch,
            confirm=require_push_confirm,
            update_status=False,
        )
        if push_result.status in (TaskStatus.CANCELLED, TaskStatus.FAILED):
            result.errors.extend(push_result.errors)
            if push_result.result_summary:
                result.errors.append(push_result.result_summary)
            self._set_task_status(task_id, push_result.status)
            return result

        # -- Paso 4: PR --
        title = pr_title or message
        body = pr_body or task.user_message or ""
        # Reload task from DB for correct state
        task = self.task_manager.get_task(task_id)
        pr_result = self.confirmation_handler.pr_with_confirmation(
            task=task,
            pr_manager=self.pr_manager,
            title=title,
            body=body,
            base=self.base_branch,
            confirm=require_pr_confirm,
            update_status=False,
        )
        if pr_result.status in (TaskStatus.CANCELLED, TaskStatus.FAILED):
            result.errors.extend(pr_result.errors)
            if pr_result.result_summary:
                result.errors.append(pr_result.result_summary)
            self._set_task_status(task_id, pr_result.status)
            return result

        # -- Exito --
        result.pr_number = pr_result.metadata.get("pr_number")
        result.pr_url = pr_result.metadata.get("pr_url")
        result.success = True
        self.task_manager.update_task_status(task_id, TaskStatus.COMPLETED)
        logger.info(
            f"GitHub workflow complete: PR #{result.pr_number} → {result.pr_url}"
        )
        return result

    def _set_task_status(self, task_id: str, status: TaskStatus):
        try:
            self.task_manager.update_task_status(task_id, status)
        except Exception:
            pass

    def _fail_task(self, task_id: str, result: GitHubWorkflowResult):
        self._set_task_status(task_id, TaskStatus.FAILED)
