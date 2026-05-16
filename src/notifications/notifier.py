"""Sistema de notificacion (T-15).

Gestiona el envio de notificaciones a multiples canales
(telegram, web, email) basado en eventos del orquestador.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict

from src.core.models import (
    Notification,
    NotificationChannel,
    NotificationPriority,
)


@dataclass
class NotificationRecord:
    """Registro de una notificacion enviada."""
    id: str
    channel: NotificationChannel
    priority: NotificationPriority
    title: str
    body: str
    task_id: Optional[str] = None
    action_required: bool = False
    sent_at: Optional[datetime] = None
    success: bool = False
    error: Optional[str] = None


class NotificationChannelBase(ABC):
    """Interfaz base para canales de notificacion."""

    @abstractmethod
    async def send(self, notification: Notification) -> bool:
        """Enviar una notificacion.

        Args:
            notification: La notificacion a enviar.

        Returns:
            True si el envio fue exitoso.
        """

    @property
    @abstractmethod
    def channel_type(self) -> NotificationChannel:
        """Tipo del canal."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Verificar si el canal esta habilitado."""


class ConsoleChannel(NotificationChannelBase):
    """Canal de consola (para desarrollo y testing)."""

    @property
    def channel_type(self) -> NotificationChannel:
        return NotificationChannel.WEB  # Reuse WEB as fallback for console

    def is_enabled(self) -> bool:
        return True

    async def send(self, notification: Notification) -> bool:
        icon = {
            NotificationPriority.LOW: "[INFO]",
            NotificationPriority.NORMAL: "[NOTE]",
            NotificationPriority.HIGH: "[WARN]",
            NotificationPriority.URGENT: "[ALERT]",
        }.get(notification.priority, "[NOTE]")

        action_str = " [ACCION REQUERIDA]" if notification.action_required else ""
        task_str = f" [Task: {notification.task_id}]" if notification.task_id else ""
        print(f"{icon} {notification.title}{task_str}{action_str}")
        print(f"  {notification.body}")
        return True


class Notifier:
    """Orquestador de notificaciones.

    Registra canales de notificacion y despacha eventos
    a los canales correspondientes segun prioridad y configuracion.
    """

    def __init__(self):
        self._channels: Dict[NotificationChannel, NotificationChannelBase] = {}
        self._history: List[NotificationRecord] = []
        self._default_priority = NotificationPriority.NORMAL

    def register(self, channel: NotificationChannelBase):
        """Registrar un canal de notificacion.

        Args:
            channel: Implementacion de NotificationChannelBase.
        """
        self._channels[channel.channel_type] = channel

    def unregister(self, channel_type: NotificationChannel):
        """Eliminar un canal registrado."""
        self._channels.pop(channel_type, None)

    def get_channels(self) -> List[NotificationChannel]:
        """Listar canales registrados."""
        return list(self._channels.keys())

    async def notify(
        self,
        title: str,
        body: str,
        channel: Optional[NotificationChannel] = None,
        priority: Optional[NotificationPriority] = None,
        task_id: Optional[str] = None,
        action_required: bool = False,
    ) -> List[NotificationRecord]:
        """Enviar una notificacion a uno o todos los canales.

        Args:
            title: Titulo de la notificacion.
            body: Cuerpo del mensaje.
            channel: Canal especifico. Si None, envia a todos.
            priority: Prioridad. Si None, usa la default.
            task_id: ID de tarea asociada.
            action_required: Si requiere accion del usuario.

        Returns:
            Lista de NotificationRecord con resultados.
        """
        priority = priority or self._default_priority
        targets = (
            [channel] if channel else list(self._channels.keys())
        )

        records: List[NotificationRecord] = []

        for ch_type in targets:
            ch = self._channels.get(ch_type)
            if ch is None or not ch.is_enabled():
                continue

            notification = Notification(
                id=str(uuid.uuid4())[:8],
                channel=ch_type,
                task_id=task_id,
                priority=priority,
                title=title,
                body=body,
                action_required=action_required,
            )

            try:
                success = await ch.send(notification)
                error_msg = None
            except Exception as e:
                success = False
                error_msg = str(e)

            record = NotificationRecord(
                id=notification.id,
                channel=ch_type,
                priority=priority,
                title=title,
                body=body,
                task_id=task_id,
                action_required=action_required,
                sent_at=datetime.utcnow(),
                success=success,
                error=None if success else error_msg,
            )
            records.append(record)
            self._history.append(record)

        return records

    async def notify_task_event(
        self,
        task_id: str,
        event: str,
        details: str,
        priority: Optional[NotificationPriority] = None,
    ) -> List[NotificationRecord]:
        """Enviar notificacion basada en un evento de tarea.

        Args:
            task_id: ID de la tarea.
            event: Tipo de evento (e.g. "completed", "failed").
            details: Detalles del evento.
            priority: Prioridad override.

        Returns:
            Lista de NotificationRecord.
        """
        event_titles = {
            "task_created": "Tarea creada",
            "task_started": "Tarea iniciada",
            "task_completed": "Tarea completada",
            "task_failed": "Tarea fallida",
            "task_blocked": "Tarea bloqueada",
            "waiting_confirmation": "Esperando confirmacion",
        }

        title = event_titles.get(event, f"Evento: {event}")
        prio = priority or self._default_priority

        if event == "task_failed":
            prio = NotificationPriority.HIGH
        elif event == "waiting_confirmation":
            prio = NotificationPriority.URGENT

        return await self.notify(
            title=title,
            body=details,
            task_id=task_id,
            priority=prio,
            action_required=(event == "waiting_confirmation"),
        )

    def get_history(
        self,
        task_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[NotificationRecord]:
        """Obtener historial de notificaciones.

        Args:
            task_id: Filtrar por tarea.
            limit: Maximo de registros.

        Returns:
            Lista de NotificationRecord.
        """
        history = self._history
        if task_id:
            history = [r for r in history if r.task_id == task_id]
        return history[-limit:]

    def clear_history(self):
        """Limpiar historial de notificaciones."""
        self._history.clear()
