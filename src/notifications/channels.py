"""Bot de Telegram (T-16).

Canal de notificacion via Telegram Bot API.
Soporta envio de mensajes y recepcion de respuestas
(interacciones del usuario via botones inline).
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional, Dict, Any

from src.core.models import Notification, NotificationChannel, NotificationPriority
from src.notifications.notifier import NotificationChannelBase


class TelegramChannel(NotificationChannelBase):
    """Canal de notificacion via Telegram Bot API.

    Usa polling HTTP directo al API de Telegram (sin librerias externas).
    Soporta mensajes de texto con botones inline para acciones.
    """

    API_BASE = "https://api.telegram.org/bot{token}"

    def __init__(
        self,
        token: str,
        chat_id: str,
        enabled: bool = True,
        parse_mode: str = "Markdown",
    ):
        """
        Args:
            token: Token del bot de Telegram.
            chat_id: ID del chat destino.
            enabled: Si el canal esta activo.
            parse_mode: Formato de parseo (Markdown, HTML, MarkdownV2).
        """
        self.token = token
        self.chat_id = chat_id
        self._enabled = enabled
        self.parse_mode = parse_mode
        self._last_update_id: Optional[int] = None

    @property
    def channel_type(self) -> NotificationChannel:
        return NotificationChannel.TELEGRAM

    def is_enabled(self) -> bool:
        return self._enabled

    def _api_url(self, method: str) -> str:
        return self.API_BASE.format(token=self.token) + f"/{method}"

    def _post(self, method: str, data: dict) -> Optional[dict]:
        """Hacer POST al API de Telegram."""
        url = self._api_url(method)
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise ConnectionError(f"Telegram API error: {e}")

    def _get(self, method: str, params: Optional[dict] = None) -> Optional[dict]:
        """Hacer GET al API de Telegram."""
        url = self._api_url(method)
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise ConnectionError(f"Telegram API error: {e}")

    async def send(self, notification: Notification) -> bool:
        """Enviar notificacion como mensaje de Telegram.

        Para notificaciones urgentes o que requieren accion,
        agrega botones inline (approve/reject).
        """
        icon_map = {
            NotificationPriority.LOW: "\U0001F4CB",  # clipboard
            NotificationPriority.NORMAL: "\U0001F4E2",  # loudspeaker
            NotificationPriority.HIGH: "\U000026A0",  # warning
            NotificationPriority.URGENT: "\U0001F6A8",  # rotating light
        }
        icon = icon_map.get(notification.priority, "\U0001F4E2")

        task_str = f"\nTask: `{notification.task_id}`" if notification.task_id else ""
        action_str = "\n\n[ACCION REQUERIDA]" if notification.action_required else ""

        text = f"{icon} *{notification.title}*{task_str}{action_str}\n\n{notification.body}"

        # Truncar si excede limite de Telegram (4096 chars).
        if len(text) > 4000:
            text = text[:3997] + "..."

        data: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": self.parse_mode,
        }

        # Agregar botones inline si requiere accion.
        if notification.action_required:
            data["reply_markup"] = json.dumps({
                "inline_keyboard": [
                    [
                        {"text": "Aprobar", "callback_data": f"approve:{notification.id}"},
                        {"text": "Rechazar", "callback_data": f"reject:{notification.id}"},
                    ],
                ],
            })

        result = self._post("sendMessage", data)
        return result.get("ok", False) if result else False

    def send_photo(
        self,
        photo_url: str,
        caption: str = "",
    ) -> bool:
        """Enviar una foto con caption opcional."""
        data = {
            "chat_id": self.chat_id,
            "photo": photo_url,
        }
        if caption:
            data["caption"] = caption
        result = self._post("sendPhoto", data)
        return result.get("ok", False) if result else False

    def get_updates(self, offset: Optional[int] = None) -> list[dict]:
        """Obtener updates del bot (mensajes, callbacks)."""
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

    def answer_callback(
        self,
        callback_query_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Responder a un callback de boton inline."""
        data = {
            "callback_query_id": callback_query_id,
        }
        if text:
            data["text"] = text
        if show_alert:
            data["show_alert"] = True
        result = self._post("answerCallbackQuery", data)
        return result.get("ok", False) if result else False

    def get_me(self) -> Optional[dict]:
        """Verificar que el bot esta autenticado."""
        result = self._get("getMe")
        if result and result.get("ok"):
            return result.get("result")
        return None
