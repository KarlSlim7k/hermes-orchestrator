"""Interfaz base de agentes CLI (T-06).

Define el contrato que todo adaptador de agente debe implementar
para integrarse con el orquestador Hermes.
"""

import subprocess
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Iterator


@dataclass
class AgentProgress:
    """Represents a progress update from an agent's stdout."""
    percentage: float
    message: str


@dataclass
class AgentResult:
    """Resultado de la ejecucion de un agente."""
    status: str  # "completed" | "failed"
    summary: str
    files_modified: list[str] = field(default_factory=list)
    tests_passed: bool = False
    tests_failed: bool = False
    errors: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    raw_output: str = ""
    exit_code: int = 0
    session_id: Optional[str] = None
    tokens_total: Optional[int] = None
    cost: Optional[float] = None


@dataclass
class AgentStreamEvent:
    """Un evento emitido durante el stream de un agente en vivo."""
    event_type: str  # "text", "tool", "progress", "step_finish", etc.
    data: dict


class BaseAgent(ABC):
    """Interfaz abstracta para adaptadores de agentes CLI.

    Cada agente CLI (Codex, OpenCode, Kiro, etc.) debe implementar
    esta interfaz para poder ser invocado por el orquestador.
    """

    def __init__(
        self,
        cli_command: str,
        workdir: Optional[str] = None,
        timeout_seconds: int = 600,
        auto_approve: bool = False,
    ):
        """
        Args:
            cli_command: Comando base del agente (e.g. "opencode run").
            workdir: Directorio de trabajo del agente.
            timeout_seconds: Timeout maximo en segundos.
            auto_approve: Si True, auto-aprobar permisos del agente.
        """
        self.cli_command = cli_command
        self.workdir = workdir
        self.timeout_seconds = timeout_seconds
        self.auto_approve = auto_approve

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> list[str]:
        """Construir el comando completo para invocar al agente.

        Args:
            prompt: Instruccion para el agente.
            workdir: Directorio de trabajo (override del default).
            extra_args: Argumentos adicionales para el CLI.

        Returns:
            Lista de argumentos para subprocess.
        """

    @abstractmethod
    def parse_result(self, stdout: str, stderr: str, exit_code: int) -> AgentResult:
        """Parsear la salida del agente en un AgentResult estructurado.

        Args:
            stdout: Salida estandar del agente.
            stderr: Salida de error del agente.
            exit_code: Codigo de salida del proceso.

        Returns:
            AgentResult con el resultado estructurado.
        """

    def parse_progress(self, line: str) -> Optional[AgentProgress]:
        """Parsear una linea de stdout como progreso.

        Por defecto busca el patron [PROGRESS] <pct> - <msg>.
        Los agentes pueden override para detectar formatos propios.

        Args:
            line: Una linea de stdout.

        Returns:
            AgentProgress si la linea contiene progreso, None si no.
        """
        match = re.match(r"\[PROGRESS\]\s+(\d+(?:\.\d+)?)\s*[-:]\s*(.*)", line)
        if match:
            return AgentProgress(
                percentage=float(match.group(1)),
                message=match.group(2).strip(),
            )
        return None

    def run_sync(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> AgentResult:
        """Ejecutar el agente de forma sincrona y retornar el resultado.

        Args:
            prompt: Instruccion para el agente.
            workdir: Directorio de trabajo (override del default).
            extra_args: Argumentos adicionales.

        Returns:
            AgentResult con el resultado.
        """
        cmd = self.build_command(prompt, workdir, extra_args)
        effective_workdir = workdir or self.workdir

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=effective_workdir,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                status="failed",
                summary=f"Agent timed out after {self.timeout_seconds}s",
                exit_code=-1,
            )

        return self.parse_result(proc.stdout, proc.stderr, proc.returncode)

    def stream_events(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> Iterator[AgentStreamEvent]:
        """Ejecutar el agente y hacer yield de eventos en tiempo real.

        Los subprocesos deben implementar esto para emitir eventos
        de progreso, texto, herramientas, etc.

        Args:
            prompt: Instruccion para el agente.
            workdir: Directorio de trabajo.
            extra_args: Argumentos adicionales.

        Yields:
            AgentStreamEvent para cada evento del agente.
        """
        cmd = self.build_command(prompt, workdir, extra_args)
        effective_workdir = workdir or self.workdir

        with subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=effective_workdir,
        ) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                progress = self.parse_progress(line)
                if progress:
                    yield AgentStreamEvent(
                        event_type="progress",
                        data={"percentage": progress.percentage, "message": progress.message},
                    )
                else:
                    yield AgentStreamEvent(
                        event_type="stdout_line",
                        data={"line": line},
                    )

            proc.wait()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cli='{self.cli_command}', workdir='{self.workdir}')"
