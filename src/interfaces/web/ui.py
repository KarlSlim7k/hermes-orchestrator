"""Panel web basico (T-17).

Interfaz web minimalista para visualizar tareas, estado
y notificaciones del orquestador. Usa solo stdlib (http.server).
"""

import json
import html
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Callable
from datetime import datetime

from src.core.models import TaskStatus


# Plantilla HTML del panel web.
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hermes Orquestador</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; padding: 2rem; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #58a6ff; margin-bottom: 1.5rem; font-size: 1.5rem; }}
        .stats {{ display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }}
        .stat {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; flex: 1; min-width: 120px; }}
        .stat-value {{ font-size: 2rem; font-weight: bold; color: #58a6ff; }}
        .stat-label {{ font-size: 0.8rem; color: #8b949e; margin-top: 0.25rem; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
        th, td {{ padding: 0.75rem; text-align: left; border-bottom: 1px solid #30363d; }}
        th {{ background: #161b22; color: #8b949e; font-size: 0.8rem; text-transform: uppercase; }}
        td {{ font-size: 0.9rem; }}
        tr:hover td {{ background: #1c2333; }}
        .status {{ padding: 0.2rem 0.6rem; border-radius: 12px; font-size: 0.75rem; font-weight: bold; }}
        .status-pending {{ background: #2d2006; color: #d29922; }}
        .status-running {{ background: #0c2d6b; color: #58a6ff; }}
        .status-completed {{ background: #0d4429; color: #3fb950; }}
        .status-failed {{ background: #4d1215; color: #f85149; }}
        .status-blocked {{ background: #3d1f4d; color: #bc8cff; }}
        .status-waiting {{ background: #3d2e0a; color: #e3b341; }}
        .status-cancelled {{ background: #2d2006; color: #8b949e; }}
        .refresh {{ color: #58a6ff; font-size: 0.8rem; margin-top: 1rem; }}
        .empty {{ text-align: center; padding: 3rem; color: #484f58; }}
    </style>
    <script>
        setTimeout(() => location.reload(), 10000);
    </script>
</head>
<body>
    <div class="container">
        <h1>Hermes Orquestador</h1>
        <div class="stats">
            {stats}
        </div>
        <h2>Tareas Recientes</h2>
        {table}
        <p class="refresh">Auto-refresh cada 10s | Ultima actualizacion: {timestamp}</p>
    </div>
</body>
</html>"""


def status_class(status: str) -> str:
    """Mapear status a clase CSS."""
    mapping = {
        "pending": "status-pending",
        "running": "status-running",
        "completed": "status-completed",
        "failed": "status-failed",
        "blocked": "status-blocked",
        "waiting_confirmation": "status-waiting",
        "cancelled": "status-cancelled",
    }
    return mapping.get(status, "status-pending")


def render_panel(tasks: list, notifications: Optional[list] = None) -> str:
    """Renderizar el panel web completo.

    Args:
        tasks: Lista de objetos Task.
        notifications: Lista opcional de NotificationRecord.

    Returns:
        HTML string del panel.
    """
    # Calcular estadisticas.
    total = len(tasks)
    status_counts: dict = {}
    for t in tasks:
        s = t.status.value if hasattr(t.status, "value") else str(t.status)
        status_counts[s] = status_counts.get(s, 0) + 1

    running = status_counts.get("running", 0)
    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    pending = status_counts.get("pending", 0)

    stats_html = ""
    for label, value, color in [
        ("Total", total, "#58a6ff"),
        ("Pendientes", pending, "#d29922"),
        ("Ejecutando", running, "#58a6ff"),
        ("Completadas", completed, "#3fb950"),
        ("Fallidas", failed, "#f85149"),
    ]:
        stats_html += f"""<div class="stat">
            <div class="stat-value" style="color:{color}">{value}</div>
            <div class="stat-label">{label}</div>
        </div>"""

    if not tasks:
        table_html = '<div class="empty">No hay tareas registradas</div>'
    else:
        rows = ""
        for t in tasks[:20]:
            msg = html.escape((t.user_message or "")[:80])
            agent = html.escape(t.agent_name or "sin asignar")
            s = t.status.value if hasattr(t.status, "value") else str(t.status)
            rows += f"""<tr>
                <td><code>{html.escape(str(t.id))}</code></td>
                <td>{msg}</td>
                <td>{agent}</td>
                <td><span class="status {status_class(s)}">{s}</span></td>
                <td>{html.escape(str(t.created_at)[:19])}</td>
            </tr>"""

        table_html = f"""<table>
            <tr><th>ID</th><th>Mensaje</th><th>Agente</th><th>Estado</th><th>Creada</th></tr>
            {rows}
        </table>"""

    return HTML_TEMPLATE.format(
        stats=stats_html,
        table=table_html,
        timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


class WebUIHandler(BaseHTTPRequestHandler):
    """HTTP handler para el panel web."""

    task_manager = None
    notifier = None

    def do_GET(self):
        """Manejar peticiones GET."""
        if self.path == "/":
            self._serve_dashboard()
        elif self.path.startswith("/api/tasks"):
            self._serve_tasks_api()
        elif self.path.startswith("/health"):
            self._serve_health()
        else:
            self.send_error(404, "Not found")

    def _serve_dashboard(self):
        """Servir el panel HTML."""
        tasks = []
        notifications = []

        if self.task_manager:
            try:
                tasks = self.task_manager.list_tasks(limit=50)
            except Exception:
                pass

        html_content = render_panel(tasks, notifications)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_content.encode("utf-8"))

    def _serve_tasks_api(self):
        """Servir tareas como JSON."""
        tasks = []
        if self.task_manager:
            try:
                tasks = self.task_manager.list_tasks(limit=50)
            except Exception:
                pass

        data = []
        for t in tasks:
            data.append({
                "id": str(t.id),
                "user_message": t.user_message,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "agent_name": t.agent_name,
                "created_at": str(t.created_at),
                "task_type": t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type),
            })

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"tasks": data, "total": len(data)}).encode("utf-8"))

    def _serve_health(self):
        """Endpoint de salud."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "timestamp": datetime.utcnow().isoformat()}).encode("utf-8"))

    def log_message(self, format, *args):
        """Suprimir logs del servidor por defecto."""
        pass


def start_web_ui(
    host: str = "0.0.0.0",
    port: int = 8000,
    task_manager=None,
    notifier=None,
) -> HTTPServer:
    """Iniciar el servidor web del panel.

    Args:
        host: Host para escuchar.
        port: Puerto para escuchar.
        task_manager: TaskManager instance.
        notifier: Notifier instance.

    Returns:
        HTTPServer instance.
    """
    WebUIHandler.task_manager = task_manager
    WebUIHandler.notifier = notifier
    server = HTTPServer((host, port), WebUIHandler)
    return server
