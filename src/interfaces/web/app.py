"""Web App entry point (T-17bis).

Aplicacion web que conecta TaskManager + Notifier + UI
en un servidor HTTP con panel visual y API REST.

Endpoints:
  GET  /                    Panel visual (HTML)
  GET  /api/tasks           Lista de tareas (JSON)
  GET  /api/tasks/:id       Detalle de una tarea
  GET  /api/events/:id      Eventos de una tarea
  POST /api/tasks           Crear tarea
  POST /api/tasks/:id/start Iniciar tarea
  POST /api/tasks/:id/cancel Cancelar tarea
  GET  /api/notifications   Historial de notificaciones
  GET  /health              Health check
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Any
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from src.core.models import Task, TaskStatus, TaskType, Notification
from src.core.logging import get_logger
from src.orchestrator.task_manager import TaskManager
from src.notifications.notifier import Notifier
from src.interfaces.web.ui import render_panel

logger = get_logger("interfaces.web")


# ---------------------------------------------------------------------------
# AppHandler — class with mutable class-level references
# ---------------------------------------------------------------------------

class AppHandler(BaseHTTPRequestHandler):
    """HTTP handler con API REST + panel visual.

    Usa atributos de clase (seteados por WebApp) para acceder
    a TaskManager y Notifier, permitiendo actualizacion en runtime.
    """

    task_manager: Optional[TaskManager] = None
    notifier: Optional[Notifier] = None

    def send_error(self, code, message="Error", explain=None):
        """Override para retornar JSON en vez de HTML."""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

    # -- Routing --

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._serve_dashboard()
        elif path == "/health":
            self._serve_health()
        elif path == "/api/tasks":
            self._serve_tasks()
        elif path.startswith("/api/tasks/"):
            parts = path[len("/api/tasks/"):].split("/")
            task_id = parts[0]
            subpath = "/".join(parts[1:]) if len(parts) > 1 else ""
            if subpath == "events":
                self._serve_task_events(task_id)
            else:
                self._serve_task_detail(task_id)
        elif path == "/api/notifications":
            self._serve_notifications()
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        if path == "/api/tasks":
            self._create_task(data)
        elif path.startswith("/api/tasks/"):
            parts = path[len("/api/tasks/"):].split("/")
            task_id = parts[0]
            action = parts[1] if len(parts) > 1 else ""
            if action == "start":
                self._start_task(task_id)
            elif action == "cancel":
                self._cancel_task(task_id)
            else:
                self.send_error(404, f"Unknown action: {action}")
        else:
            self.send_error(404, "Not found")

    # -- GET handlers --

    def _serve_dashboard(self):
        tm = self.task_manager
        try:
            tasks = tm.list_tasks(limit=50) if tm else []
        except Exception:
            tasks = []
        html_content = render_panel(tasks)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_content.encode("utf-8"))

    def _serve_health(self):
        self._json_response({
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
        })

    def _serve_tasks(self):
        tm = self.task_manager
        params = parse_qs(urlparse(self.path).query)
        status_filter = params.get("status", [None])[0]
        limit = int(params.get("limit", ["50"])[0])

        try:
            if status_filter:
                st = TaskStatus(status_filter)
                tasks = tm.list_tasks(status=st, limit=limit) if tm else []
            else:
                tasks = tm.list_tasks(limit=limit) if tm else []
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)
            return

        self._json_response({
            "tasks": [self._task_to_dict(t) for t in tasks],
            "total": len(tasks),
        })

    def _serve_task_detail(self, task_id: str):
        tm = self.task_manager
        try:
            task = tm.get_task(task_id) if tm else None
            if task is None:
                self._json_response({"error": f"Task {task_id} not found"}, status=404)
                return
            self._json_response(self._task_to_dict(task))
        except Exception as e:
            self._json_response({"error": str(e)}, status=404)

    def _serve_task_events(self, task_id: str):
        tm = self.task_manager
        try:
            # Verify task exists first
            if tm:
                tm.get_task(task_id)  # raises if not found
                events = tm.get_task_events(task_id)
            else:
                events = []
            self._json_response({
                "task_id": task_id,
                "events": [
                    {
                        "id": e.id,
                        "event_type": e.event_type.value,
                        "timestamp": e.timestamp.isoformat(),
                        "message": e.message,
                        "details": e.details,
                    }
                    for e in events
                ],
            })
        except Exception as e:
            self._json_response({"error": str(e)}, status=404)

    def _serve_notifications(self):
        notif = self.notifier
        if notif is None:
            self._json_response({"notifications": [], "total": 0})
            return

        params = parse_qs(urlparse(self.path).query)
        limit = int(params.get("limit", ["50"])[0])
        task_id = params.get("task_id", [None])[0]

        try:
            history = notif.get_history(task_id=task_id, limit=limit)
            self._json_response({
                "notifications": [
                    {
                        "id": r.id,
                        "channel": r.channel.value,
                        "priority": r.priority.value,
                        "title": r.title,
                        "body": r.body,
                        "task_id": r.task_id,
                        "success": r.success,
                        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                    }
                    for r in history
                ],
                "total": len(history),
            })
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    # -- POST handlers --

    def _create_task(self, data: dict):
        tm = self.task_manager
        try:
            user_message = data.get("user_message", "")
            if not user_message:
                self._json_response({"error": "user_message is required"}, status=400)
                return

            task = Task(
                id=data.get("id", ""),
                user_message=user_message,
                task_type=TaskType(data.get("task_type", "modification")),
                agent_name=data.get("agent_name"),
                repository=data.get("repository", "."),
                branch=data.get("branch"),
                priority=int(data.get("priority", 0)),
                requires_confirmation=data.get("requires_confirmation", True),
            )
            created = tm.create_task(task) if tm else None
            if created is None:
                self._json_response({"error": "No task manager configured"}, status=500)
                return
            self._json_response(self._task_to_dict(created), status=201)
        except ValueError as e:
            self._json_response({"error": str(e)}, status=400)
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _start_task(self, task_id: str):
        tm = self.task_manager
        try:
            task = tm.update_task_status(task_id, TaskStatus.RUNNING) if tm else None
            if task is None:
                self._json_response({"error": f"Task {task_id} not found"}, status=404)
                return
            self._json_response(self._task_to_dict(task))
        except Exception as e:
            self._json_response({"error": str(e)}, status=404)

    def _cancel_task(self, task_id: str):
        tm = self.task_manager
        try:
            task = tm.update_task_status(task_id, TaskStatus.CANCELLED) if tm else None
            if task is None:
                self._json_response({"error": f"Task {task_id} not found"}, status=404)
                return
            self._json_response(self._task_to_dict(task))
        except Exception as e:
            self._json_response({"error": str(e)}, status=404)

    # -- Utilities --

    def _task_to_dict(self, task: Task) -> dict:
        return {
            "id": task.id,
            "user_message": task.user_message,
            "status": task.status.value,
            "task_type": task.task_type.value,
            "agent_name": task.agent_name,
            "repository": task.repository,
            "branch": task.branch,
            "priority": task.priority,
            "requires_confirmation": task.requires_confirmation,
            "result_summary": task.result_summary,
            "files_modified": task.files_modified,
            "errors": task.errors,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    def _json_response(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(format, *args)


# ---------------------------------------------------------------------------
# ReusableHTTPServer
# ---------------------------------------------------------------------------

class ReusableHTTPServer(HTTPServer):
    """HTTPServer con allow_reuse_address para evitar 'Address already in use'."""
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# WebApp
# ---------------------------------------------------------------------------

class WebApp:
    """Aplicacion web del orquestador.

    Combina el panel visual (ui.py) con endpoints REST para
    gestion de tareas, eventos y notificaciones.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        notifier: Optional[Notifier] = None,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        """
        Args:
            task_manager: TaskManager para gestion de tareas.
            notifier: Notifier opcional para notificaciones.
            host: Host para escuchar.
            port: Puerto para escuchar.
        """
        self.task_manager = task_manager
        self.notifier = notifier
        self.host = host
        self.port = port
        self._server: Optional[ReusableHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, background: bool = True) -> ReusableHTTPServer:
        """Iniciar el servidor web.

        Args:
            background: Si True, corre en thread separado (no bloqueante).

        Returns:
            HTTPServer instance.
        """
        # Set class-level attributes on handler (accessible to all request instances)
        AppHandler.task_manager = self.task_manager
        AppHandler.notifier = self.notifier

        self._server = ReusableHTTPServer(
            (self.host, self.port),
            AppHandler,
        )

        if background:
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
            )
            self._thread.start()
            logger.info(f"WebApp started on http://{self.host}:{self.port}")
        else:
            logger.info(f"WebApp starting on http://{self.host}:{self.port}")
            self._server.serve_forever()

        return self._server

    def stop(self):
        """Detener el servidor."""
        if self._server:
            self._server.shutdown()
            logger.info("WebApp stopped")

    def wait(self):
        """Esperar a que el thread del servidor termine (solo si background=True)."""
        if self._thread:
            self._thread.join()
