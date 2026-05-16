"""Flujo de confirmacion (T-14).

Gestiona aprobaciones del usuario antes de acciones destructivas
(commit, push, merge) con timeout y opciones de accion.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from src.core.models import Task, TaskStatus
from src.orchestrator.task_manager import TaskManager
from src.github.git_ops import GitOperations, GitError
from src.github.pr_manager import PRManager, PRError


class ConfirmAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    TIMEOUT = "timeout"


@dataclass
class ConfirmationRequest:
    """Solicitud de confirmacion al usuario."""
    task_id: str
    action: str
    description: str
    options: list[str] = field(default_factory=lambda: ["approve", "reject"])
    timeout_seconds: int = 300
    created_at: float = field(default_factory=time.time)


@dataclass
class ConfirmationResponse:
    """Respuesta a una solicitud de confirmacion."""
    action: ConfirmAction
    response_text: str = ""
    timestamp: float = field(default_factory=time.time)


class ConfirmationHandler:
    """Maneja el ciclo de confirmacion para acciones sensibles.

    Soporta confirmacion sincrona (input de consola) y asincrona
    (callback externo, e.g. Telegram button).
    """

    ACTIONS_REQUIRING_CONFIRMATION = {
        "commit": "Commit cambios al repositorio",
        "push": "Hacer push al remoto",
        "pull_request": "Crear Pull Request",
        "merge": "Merge de Pull Request",
    }

    def __init__(
        self,
        task_manager: TaskManager,
        confirm_fn: Optional[Callable[[ConfirmationRequest], ConfirmationResponse]] = None,
    ):
        """
        Args:
            task_manager: TaskManager para actualizar estados de tareas.
            confirm_fn: Funcion custom para obtener confirmacion.
                Si None, usa input de consola.
        """
        self.task_manager = task_manager
        self._confirm_fn = confirm_fn
        self._pending_requests: dict[str, ConfirmationRequest] = {}

    def needs_confirmation(self, action: str) -> bool:
        """Determinar si una accion requiere confirmacion."""
        return action in self.ACTIONS_REQUIRING_CONFIRMATION

    def create_request(
        self,
        task_id: str,
        action: str,
        description: str,
        timeout_seconds: int = 300,
    ) -> ConfirmationRequest:
        """Crear una solicitud de confirmacion."""
        request = ConfirmationRequest(
            task_id=task_id,
            action=action,
            description=description,
            timeout_seconds=timeout_seconds,
        )
        self._pending_requests[task_id] = request
        return request

    def request_confirmation(
        self,
        task_id: str,
        action: str,
        description: str,
        timeout_seconds: int = 300,
    ) -> ConfirmationResponse:
        """Solicitar y esperar confirmacion.

        Usa la funcion custom si esta disponible, sino input de consola.

        Args:
            task_id: ID de la tarea.
            action: Tipo de accion (commit, push, etc).
            description: Descripcion de lo que se va a hacer.
            timeout_seconds: Timeout en segundos.

        Returns:
            ConfirmationResponse con la decision.
        """
        request = self.create_request(task_id, action, description, timeout_seconds)

        if self._confirm_fn:
            return self._confirm_fn(request)

        return self._confirm_console(request)

    def _confirm_console(self, request: ConfirmationRequest) -> ConfirmationResponse:
        """Confirmacion via consola."""
        print(f"\n[CONFIRMAR] {request.description}")
        print(f"Accion: {request.action}")
        print(f"Timeout: {request.timeout_seconds}s")
        print("Opciones: approve / reject")

        try:
            response = input("> ").strip().lower()
            if response in ("approve", "yes", "y", "a"):
                return ConfirmationResponse(action=ConfirmAction.APPROVE)
            elif response in ("reject", "no", "n", "r"):
                return ConfirmationResponse(action=ConfirmAction.REJECT)
            else:
                return ConfirmationResponse(
                    action=ConfirmAction.REJECT,
                    response_text=f"Respuesta no reconocida: {response}",
                )
        except (EOFError, KeyboardInterrupt):
            return ConfirmationResponse(action=ConfirmAction.REJECT)

    def approve(self, task_id: str) -> ConfirmationResponse:
        """Aprobar una solicitud pendiente programaticamente."""
        if task_id in self._pending_requests:
            del self._pending_requests[task_id]
        return ConfirmationResponse(action=ConfirmAction.APPROVE)

    def reject(self, task_id: str) -> ConfirmationResponse:
        """Rechazar una solicitud pendiente programaticamente."""
        if task_id in self._pending_requests:
            del self._pending_requests[task_id]
        return ConfirmationResponse(action=ConfirmAction.REJECT)

    # -- Acciones con confirmacion integrada --

    def commit_with_confirmation(
        self,
        task: Task,
        git_ops: GitOperations,
        message: str,
        confirm: bool = True,
    ) -> Task:
        """Hacer commit con confirmacion opcional."""
        if confirm and self.needs_confirmation("commit"):
            resp = self.request_confirmation(
                task.id, "commit",
                f"Commit: {message}",
            )
            if resp.action != ConfirmAction.APPROVE:
                task.status = TaskStatus.CANCELLED
                task.errors.append(f"Commit cancelled by user: {resp.response_text}")
                self.task_manager.update_task_status(task.id, TaskStatus.CANCELLED)
                return task

        try:
            git_ops.add()
            result = git_ops.commit(message)
            if result.success:
                task.result_summary = f"Commit exitoso: {message}"
            else:
                task.errors.append(f"Commit failed: {result.stderr}")
                self.task_manager.update_task_status(task.id, TaskStatus.FAILED)
        except GitError as e:
            task.errors.append(f"Git error during commit: {str(e)}")
            self.task_manager.update_task_status(task.id, TaskStatus.FAILED)

        return task

    def push_with_confirmation(
        self,
        task: Task,
        git_ops: GitOperations,
        branch: Optional[str] = None,
        confirm: bool = True,
    ) -> Task:
        """Hacer push con confirmacion opcional."""
        if confirm and self.needs_confirmation("push"):
            resp = self.request_confirmation(
                task.id, "push",
                f"Push rama {branch or 'actual'} a origin",
            )
            if resp.action != ConfirmAction.APPROVE:
                task.status = TaskStatus.CANCELLED
                task.errors.append(f"Push cancelled by user: {resp.response_text}")
                self.task_manager.update_task_status(task.id, TaskStatus.CANCELLED)
                return task

        try:
            result = git_ops.push(branch=branch)
            if result.success:
                task.result_summary = f"Push exitoso a {branch or 'current branch'}"
            else:
                task.errors.append(f"Push failed: {result.stderr}")
                self.task_manager.update_task_status(task.id, TaskStatus.FAILED)
        except GitError as e:
            task.errors.append(f"Git error during push: {str(e)}")
            self.task_manager.update_task_status(task.id, TaskStatus.FAILED)

        return task

    def pr_with_confirmation(
        self,
        task: Task,
        pr_manager: PRManager,
        title: str,
        body: str,
        base: str = "main",
        confirm: bool = True,
    ) -> Task:
        """Crear PR con confirmacion opcional."""
        if confirm and self.needs_confirmation("pull_request"):
            resp = self.request_confirmation(
                task.id, "pull_request",
                f"Crear PR: {title} -> {base}",
            )
            if resp.action != ConfirmAction.APPROVE:
                task.status = TaskStatus.CANCELLED
                task.errors.append(f"PR cancelled by user: {resp.response_text}")
                self.task_manager.update_task_status(task.id, TaskStatus.CANCELLED)
                return task

        try:
            pr_info = pr_manager.create(
                title=title,
                body=body,
                base=base,
                head=task.branch,
            )
            task.result_summary = f"PR #{pr_info.number} creado: {pr_info.url}"
            task.metadata["pr_number"] = pr_info.number
            task.metadata["pr_url"] = pr_info.url
        except PRError as e:
            task.errors.append(f"PR error: {str(e)}")
            self.task_manager.update_task_status(task.id, TaskStatus.FAILED)

        return task
