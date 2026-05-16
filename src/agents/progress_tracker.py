"""Utilidades de deteccion de progreso (T-09).

Proporciona un ProgressTracker que consume eventos de stream_events
y calcula progreso acumulado, ETA, y notificaciones.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from src.agents.base import AgentStreamEvent


@dataclass
class ProgressState:
    """Estado actual del progreso de un agente."""
    percentage: float = 0.0
    current_step: str = ""
    total_steps: int = 0
    completed_steps: int = 0
    start_time: Optional[float] = None
    last_update_time: Optional[float] = None
    messages: list[str] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    @property
    def eta_seconds(self) -> Optional[float]:
        if self.percentage <= 0 or self.start_time is None:
            return None
        elapsed = self.elapsed_seconds
        return round(elapsed / (self.percentage / 100.0) - elapsed)

    @property
    def eta_human(self) -> str:
        eta = self.eta_seconds
        if eta is None:
            return "desconocido"
        if eta < 60:
            return f"{int(eta)}s"
        return f"{int(eta / 60)}m {int(eta % 60)}s"


class ProgressTracker:
    """Rastrea el progreso de un agente a partir de sus eventos.

    Se alimenta con los AgentStreamEvent de stream_events y mantiene
    un estado de progreso actualizado.

    Ejemplo:
        tracker = ProgressTracker()
        for event in agent.stream_events("haz algo"):
            tracker.update(event)
            print(f"{tracker.state.percentage}%: {tracker.state.current_step}")
    """

    def __init__(self):
        self.state = ProgressState()
        self._started = False

    def update(self, event: AgentStreamEvent) -> bool:
        """Actualizar el estado de progreso con un evento.

        Returns True si el evento modifico el estado de progreso.
        """
        changed = False

        if not self._started:
            self.state.start_time = time.time()
            self._started = True

        self.state.last_update_time = time.time()

        if event.event_type == "progress":
            data = event.data
            self.state.percentage = data.get("percentage", self.state.percentage)
            self.state.current_step = data.get("message", self.state.current_step)
            changed = True

        elif event.event_type == "stdout_line":
            line = event.data.get("line", "")
            self.state.messages.append(line)

            # Detectar inicio de nuevos pasos en la salida.
            step_markers = [
                "step", "phase", "analizando", "generando",
                "creando", "escribiendo", "test", "compilando",
            ]
            for marker in step_markers:
                if marker.lower() in line.lower():
                    self.state.completed_steps += 1
                    self.state.current_step = line.strip()
                    changed = True
                    break

        elif event.event_type == "step_finish":
            self.state.percentage = 100.0
            changed = True

        elif event.event_type == "step_start":
            self.state.completed_steps += 1
            changed = True

        return changed

    def reset(self):
        """Reiniciar el tracker para un nuevo agente."""
        self.state = ProgressState()
        self._started = False
