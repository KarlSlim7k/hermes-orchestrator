"""Bot de Telegram — interfaz receptora de comandos (T-16bis).

Recibe mensajes del usuario via Telegram polling, los clasifica con
el IntentRouter, crea tareas, las despacha al agente correspondiente,
y notifica el resultado.

Soporta:
- Mensajes naturales → enrutados automaticamente
- Comandos: /help, /status, /tasks, /cancel
- Confirmaciones inline (aprobar/rechazar commit, push, PR)
"""

import asyncio
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional, Callable, Awaitable

from src.core.models import Task, TaskStatus, TaskType
from src.core.logging import get_logger
from src.orchestrator.router import IntentRouter, NoAgentAvailableError
from src.orchestrator.task_manager import TaskManager
from src.notifications.notifier import Notifier, ConsoleChannel
from src.notifications.channels import TelegramChannel
from src.core.models import NotificationChannel as NC

logger = get_logger("interfaces.telegram")

# ---------------------------------------------------------------------------
# Comandos del bot
# ---------------------------------------------------------------------------

COMMANDS = {
    "help": "Muestra los comandos disponibles",
    "status": "Estado de la ultima tarea",
    "tasks": "Lista las tareas recientes (max 5)",
    "cancel": "Cancela la tarea en ejecucion",
}

HELP_TEXT = """*Hermes Orquestador* 🤖

Envia una orden en lenguaje natural y yo la clasifico y despacho al agente adecuado.

*Ejemplos:*
- `Analiza el codigo en src/`
- `Crea un modulo de logging`
- `Corre los tests`
- `Commitea los cambios`
- `Crea un PR a main`

*Comandos:*
""" + "\n".join(f"/{cmd} — {desc}" for cmd, desc in COMMANDS.items())

# ---------------------------------------------------------------------------
# Task Executor
# ---------------------------------------------------------------------------


class TaskExecutor:
    """Ejecuta una tarea ruteada con el agente correspondiente.

    Orquesta el flujo: PENDING → RUNNING → COMPLETED/FAILED,
    notificando en cada transicion.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        agent_registry: dict,
        notifier: Notifier,
    ):
        """
        Args:
            task_manager: TaskManager para persistencia.
            agent_registry: Dict {agent_name: BaseAgent instance}.
            notifier: Notifier para enviar notificaciones.
        """
        self.task_manager = task_manager
        self.agent_registry = agent_registry
        self.notifier = notifier

    async def execute(self, task: Task) -> Task:
        """Ejecutar una tarea de forma async.

        Args:
            task: Tarea en estado PENDING.

        Returns:
            Task actualizada con resultado.
        """
        task_id = str(task.id)

        # PENDING → RUNNING
        self.task_manager.update_task_status(task_id, TaskStatus.RUNNING)
        await self.notifier.notify_task_event(
            task_id, "task_started",
            f"Agente: {task.agent_name}\nTipo: {task.task_type.value}",
        )

        # Ejecutar con agente
        agent = self.agent_registry.get(task.agent_name)
        if agent is None:
            return self._fail_task(task_id, f"Agent '{task.agent_name}' not in registry")

        try:
            # run_sync es bloqueante; ejecutar en thread pool para no bloquear el bot
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: agent.run_sync(
                    prompt=task.user_message,
                    workdir=task.repository,
                ),
            )
        except Exception as e:
            return self._fail_task(task_id, f"Agent execution error: {e}")

        # Actualizar tarea con resultado
        if result.status == "completed":
            task = self.task_manager.update_task_status(task_id, TaskStatus.COMPLETED)
            task.result_summary = result.summary
            task.files_modified = result.files_modified
            self._save_task_results(task_id, result)
            await self.notifier.notify_task_event(
                task_id, "task_completed",
                result.summary or "Tarea completada exitosamente",
            )
        else:
            task = self._fail_task(task_id, result.summary or "Agente fallo")
            if result.errors:
                task.errors = result.errors

        return task

    def _fail_task(self, task_id: str, error_msg: str) -> Task:
        """Marcar tarea como fallida."""
        task = self.task_manager.update_task_status(task_id, TaskStatus.FAILED)
        task.errors.append(error_msg)
        self._save_task_results(task_id, None, error=error_msg)
        asyncio.create_task(
            self.notifier.notify_task_event(
                task_id, "task_failed", error_msg,
            )
        )
        return task

    def _save_task_results(self, task_id: str, result, error: Optional[str] = None):
        """Persistir resumen y errores en la tabla de tareas."""
        try:
            task = self.task_manager.get_task(task_id)
            if result:
                task.result_summary = result.summary
                task.files_modified = result.files_modified
            if error:
                task.errors.append(error)
            # Actualizar en DB (no expuesto directamente, pero se puede
            # hacer con un update en task_manager si se necesita).
        except Exception:
            pass  # No fatal si falla la persistencia extra

# ---------------------------------------------------------------------------
# TelegramBot
# ---------------------------------------------------------------------------


class TelegramBot:
    """Bot de Telegram para recibir ordenes y gestionar tareas.

    Usa polling HTTP directo (sin dependencias externas como python-telegram-bot).
    """

    API_BASE = "https://api.telegram.org/bot{token}"

    def __init__(
        self,
        token: str,
        router: IntentRouter,
        executor: TaskExecutor,
        notifier: Notifier,
        chat_id: Optional[str] = None,
        poll_interval: float = 1.0,
        default_repository: str = ".",
    ):
        """
        Args:
            token: Token del bot de Telegram.
            router: IntentRouter para clasificar mensajes.
            executor: TaskExecutor para ejecutar tareas.
            notifier: Notifier para enviar respuestas.
            chat_id: Si se setea, solo responde a este chat.
            poll_interval: Segundos entre polls.
            default_repository: Repo path por defecto si no hay tareas previas.
        """
        self.token = token
        self.router = router
        self.executor = executor
        self.notifier = notifier
        self.chat_id = chat_id
        self.poll_interval = poll_interval
        self._running = False
        self._last_update_id: Optional[int] = None
        self._pending_confirmations: dict = {}  # callback_data → task_id
        self._default_repository = default_repository

    def _api_url(self, method: str) -> str:
        return self.API_BASE.format(token=self.token) + f"/{method}"

    def _post(self, method: str, data: dict) -> Optional[dict]:
        url = self._api_url(method)
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            logger.error(f"Telegram API POST error: {e}")
            return None

    def _get(self, method: str, params: Optional[dict] = None) -> Optional[dict]:
        url = self._api_url(method)
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            logger.error(f"Telegram API GET error: {e}")
            return None

    # -- Metodos de envio --

    def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
        """Enviar mensaje de texto."""
        # Truncar si excede 4096 chars
        if len(text) > 4000:
            text = text[:3997] + "..."
        result = self._post("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        })
        return result.get("ok", False) if result else False

    def send_message_with_buttons(
        self, chat_id: str, text: str, buttons: list[list[dict]]
    ) -> bool:
        """Enviar mensaje con botones inline."""
        result = self._post("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps({"inline_keyboard": buttons}),
        })
        return result.get("ok", False) if result else False

    def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        """Responder a un callback de boton inline."""
        result = self._post("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text,
        })
        return result.get("ok", False) if result else False

    def set_commands(self, commands: Optional[list[dict]] = None) -> bool:
        """Registrar comandos en el bot (se muestran en el menu)."""
        if commands is None:
            commands = [{"command": cmd, "description": desc} for cmd, desc in COMMANDS.items()]
        result = self._post("setMyCommands", {"commands": json.dumps(commands)})
        return result.get("ok", False) if result else False

    # -- Polling --

    def get_updates(self, offset: Optional[int] = None) -> list[dict]:
        """Obtener updates del bot."""
        params: dict = {"timeout": 30}
        if offset is not None:
            params["offset"] = offset
        elif self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1

        result = self._get("getUpdates", params)
        if result and result.get("ok"):
            updates = result.get("result", [])
            if updates:
                self._last_update_id = updates[-1].get("update_id")
            return updates
        return []

    # -- Procesamiento de mensajes --

    def _is_allowed_chat(self, chat_id: str) -> bool:
        if self.chat_id is None:
            return True
        return str(chat_id) == str(self.chat_id)

    async def handle_message(self, chat_id: str, text: str, message_id: int) -> None:
        """Procesar un mensaje de texto del usuario."""
        text = text.strip()
        logger.info(f"Message from {chat_id}: {text[:100]}")

        # Comandos especiales
        if text.startswith("/"):
            await self._handle_command(chat_id, text, message_id)
            return

        # Mensaje normal → enrutamiento
        try:
            # Intentar obtener repo de la ultima tarea; fallback al default
            repo = self._default_repository
            try:
                last_tasks = self.router.task_manager.list_tasks(limit=1)
                if last_tasks:
                    repo = last_tasks[0].repository
            except Exception:
                pass

            task = self.router.route(
                user_message=text,
                repository=repo,
                priority=0,
            )
        except NoAgentAvailableError as e:
            self.send_message(chat_id, f"❌ *Error:* No hay agente disponible para esta tarea.\n\n`{e}`")
            return
        except Exception as e:
            self.send_message(chat_id, f"❌ *Error al procesar tu orden:*\n\n`{e}`")
            return

        # Confirmar recepcion
        confirm_msg = (
            f"✅ *Tarea recibida*\n\n"
            f"ID: `{task.id}`\n"
            f"Tipo: `{task.task_type.value}`\n"
            f"Agente: `{task.agent_name}`\n"
            f"Confianza: {task.metadata.get('intent_confidence', '?')}\n"
        )
        if task.requires_confirmation:
            confirm_msg += "\n⚠️ *Requiere tu confirmacion antes de ejecutar.*"
            # Enviar botones de confirmar
            cb_id = f"confirm:{task.id}"
            self._pending_confirmations[cb_id] = task.id
            self.send_message_with_buttons(chat_id, confirm_msg, [
                [
                    {"text": "✅ Ejecutar", "callback_data": f"exec:{task.id}"},
                    {"text": "❌ Cancelar", "callback_data": f"cancel:{task.id}"},
                ],
            ])
        else:
            self.send_message(chat_id, confirm_msg + "\n🔄 *Ejecutando...*")

            # Ejecutar de forma async
            asyncio.create_task(self._execute_and_notify(chat_id, task))

    async def _execute_and_notify(self, chat_id: str, task: Task) -> None:
        """Ejecutar tarea y notificar resultado al chat."""
        try:
            result_task = await self.executor.execute(task)
            status = result_task.status.value
            icon = {"completed": "✅", "failed": "❌"}.get(status, "ℹ️")

            summary = result_task.result_summary or "(sin resumen)"
            files = result_task.files_modified
            errors = result_task.errors

            msg = f"{icon} *Tarea `{task.id}` — {status}*\n\n{summary}"
            if files:
                msg += f"\n\n📁 *Archivos modificados:*\n" + "\n".join(f"`{f}`" for f in files[:10])
            if errors:
                msg += f"\n\n⚠️ *Errores:*\n" + "\n".join(f"`{e}`" for e in errors[:5])

            self.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Task execution failed for {task.id}: {e}")
            self.send_message(chat_id, f"❌ *Error ejecutando tarea `{task.id}`:*\n\n`{e}`")

    async def _handle_command(self, chat_id: str, text: str, message_id: int) -> None:
        """Procesar un comando /help, /status, /tasks, /cancel."""
        parts = text.split()
        cmd = parts[0][1:].lower()  # sin el /

        if cmd == "help":
            self.send_message(chat_id, HELP_TEXT)

        elif cmd == "status":
            tasks = self.router.task_manager.list_tasks(limit=1)
            if not tasks:
                self.send_message(chat_id, "📋 No hay tareas registradas.")
            else:
                t = tasks[0]
                self.send_message(
                    chat_id,
                    f"📊 *Ultima tarea:*\n\n"
                    f"ID: `{t.id}`\n"
                    f"Tipo: `{t.task_type.value}`\n"
                    f"Estado: `{t.status.value}`\n"
                    f"Agente: `{t.agent_name or 'sin asignar'}`\n"
                    f"Creada: `{str(t.created_at)[:19]}`",
                )

        elif cmd == "tasks":
            tasks = self.router.task_manager.list_tasks(limit=5)
            if not tasks:
                self.send_message(chat_id, "📋 No hay tareas registradas.")
                return
            msg = "📋 *Tareas recientes:*\n\n"
            for t in tasks:
                icon = {"completed": "✅", "failed": "❌", "running": "🔄"}.get(
                    t.status.value, "⏳"
                )
                msg += f"{icon} `{t.id}` — {t.task_type.value} — *{t.status.value}*\n"
                if t.user_message:
                    msg += f"   _{t.user_message[:60]}_\n"
            self.send_message(chat_id, msg)

        elif cmd == "cancel":
            running = self.router.task_manager.list_tasks(status=TaskStatus.RUNNING, limit=1)
            if not running:
                self.send_message(chat_id, "ℹ️ No hay tareas en ejecucion para cancelar.")
                return
            t = running[0]
            self.router.task_manager.update_task_status(str(t.id), TaskStatus.CANCELLED)
            self.send_message(chat_id, f"🛑 Tarea `{t.id}` cancelada.")

        else:
            self.send_message(chat_id, f"❓ Comando desconocido: `/{cmd}`\n\nUsa /help para ver los comandos disponibles.")

    async def handle_callback(self, callback_query: dict) -> None:
        """Procesar un callback de boton inline."""
        cb_id = callback_query.get("id", "")
        data = callback_query.get("data", "")
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

        if not data:
            return

        # Parse callback data: "action:task_id"
        parts = data.split(":", 1)
        if len(parts) != 2:
            self.answer_callback(cb_id, "Formato invalido")
            return

        action, task_id = parts

        if action == "exec":
            self.answer_callback(cb_id, "Ejecutando tarea...")
            task = self.router.task_manager.get_task(task_id)
            asyncio.create_task(self._execute_and_notify(chat_id, task))

        elif action == "cancel":
            self.answer_callback(cb_id, "Tarea cancelada")
            try:
                self.router.task_manager.update_task_status(task_id, TaskStatus.CANCELLED)
                self.send_message(chat_id, f"🛑 Tarea `{task_id}` cancelada.")
            except Exception:
                pass

        elif action == "confirm":
            # Alias de exec para compatibilidad
            self.answer_callback(cb_id, "Ejecutando tarea...")
            task = self.router.task_manager.get_task(task_id)
            asyncio.create_task(self._execute_and_notify(chat_id, task))

    # -- Loop principal --

    async def poll_once(self) -> None:
        """Obtener y procesar un batch de updates."""
        updates = self.get_updates()
        for update in updates:
            # Mensaje de texto
            message = update.get("message")
            if message:
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")
                msg_id = message.get("message_id", 0)
                if self._is_allowed_chat(chat_id):
                    await self.handle_message(chat_id, text, msg_id)

            # Callback de boton inline
            callback = update.get("callback_query")
            if callback:
                await self.handle_callback(callback)

    async def run(self) -> None:
        """Loop principal del bot con polling."""
        self._running = True
        logger.info("TelegramBot starting...")

        # Verificar conexion
        me = self._get("getMe")
        if me and me.get("ok"):
            bot_name = me["result"].get("first_name", "Bot")
            logger.info(f"Connected as @{me['result'].get('username', '?')} ({bot_name})")
            self.set_commands()
        else:
            logger.error("Failed to connect to Telegram API. Check your token.")
            return

        while self._running:
            try:
                await self.poll_once()
            except Exception as e:
                logger.error(f"Poll error: {e}")
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Detener el polling."""
        self._running = False
        logger.info("TelegramBot stopping...")
