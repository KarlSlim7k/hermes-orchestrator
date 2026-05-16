"""Manejo de errores de agentes (T-10).

Clasifica, reporta y maneja errores que ocurren durante la
ejecucion de agentes CLI.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from src.agents.base import AgentResult


class ErrorCategory(str, Enum):
    """Categorias de errores de agentes."""
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    AGENT_CRASH = "agent_crash"
    INVALID_OUTPUT = "invalid_output"
    RATE_LIMIT = "rate_limit"
    OUT_OF_CONTEXT = "out_of_context"
    UNKNOWN = "unknown"


@dataclass
class AgentError:
    """Representa un error clasificado de un agente."""
    category: ErrorCategory
    message: str
    recoverable: bool = False
    suggestion: str = ""
    raw_error: str = ""


# Patrones de reconocimiento de errores comunes.
_ERROR_PATTERNS = [
    {
        "pattern": r"(?:timed?\s*out|timeout)",
        "category": ErrorCategory.TIMEOUT,
        "recoverable": True,
        "suggestion": "Incrementa timeout_seconds o simplifica la tarea.",
    },
    {
        "pattern": r"(?:permission denied|access denied|no\s+permission|approval)",
        "category": ErrorCategory.PERMISSION_DENIED,
        "recoverable": True,
        "suggestion": "Usa auto_approve=True o revisa la configuracion de sandbox.",
    },
    {
        "pattern": r"(?:segmentation fault|segfault|core dump|abort|panic)",
        "category": ErrorCategory.AGENT_CRASH,
        "recoverable": False,
        "suggestion": "Reinicia el agente. Si persiste, verifica la instalacion.",
    },
    {
        "pattern": r"(?:rate limit|too many requests|quota exceeded|429)",
        "category": ErrorCategory.RATE_LIMIT,
        "recoverable": True,
        "suggestion": "Espera antes de reintentar. Revisa los limites de tu proveedor.",
    },
    {
        "pattern": r"(?:context length|token limit|too long|context window)",
        "category": ErrorCategory.OUT_OF_CONTEXT,
        "recoverable": True,
        "suggestion": "Reduce el tamano del repositorio o divide la tarea.",
    },
]


class ErrorHandler:
    """Clasifica y gestiona errores de ejecucion de agentes."""

    @staticmethod
    def classify(result: AgentResult) -> list[AgentError]:
        """Clasificar los errores de un AgentResult.

        Analiza stderr, errores registrados y codigo de salida
        para determinar las categorias de error.

        Args:
            result: El resultado de la ejecucion del agente.

        Returns:
            Lista de AgentError clasificados.
        """
        errors: list[AgentError] = []
        combined_text = "\n".join(result.errors + [result.raw_output])
        if result.details.get("stderr"):
            combined_text += "\n" + result.details["stderr"]

        classified_patterns: set[ErrorCategory] = set()

        for pattern_info in _ERROR_PATTERNS:
            if re.search(pattern_info["pattern"], combined_text, re.IGNORECASE):
                cat = pattern_info["category"]
                if cat not in classified_patterns:
                    classified_patterns.add(cat)
                    errors.append(AgentError(
                        category=cat,
                        message=f"Error detectado: {cat.value}",
                        recoverable=pattern_info["recoverable"],
                        suggestion=pattern_info["suggestion"],
                        raw_error=combined_text[:500],
                    ))

        # Errores no clasificados van como UNKNOWN.
        if result.status == "failed" and not errors:
            errors.append(AgentError(
                category=ErrorCategory.UNKNOWN,
                message=f"Agente fallo con codigo {result.exit_code}",
                recoverable=False,
                suggestion="Revisa los logs del agente para mas detalles.",
                raw_error=combined_text[:500],
            ))

        return errors

    @staticmethod
    def is_recoverable(result: AgentResult) -> bool:
        """Determinar si los errores del resultado son recuperables."""
        classified = ErrorHandler.classify(result)
        if not classified:
            return False
        return any(e.recoverable for e in classified)

    @staticmethod
    def build_report(result: AgentResult) -> str:
        """Generar un reporte legible de los errores encontrados.

        Args:
            result: El resultado de la ejecucion.

        Returns:
            String con el reporte de errores.
        """
        if result.status == "completed":
            return "Sin errores."

        classified = ErrorHandler.classify(result)
        if not classified:
            return f"Error desconocido (exit code: {result.exit_code})"

        lines: list[str] = []
        for i, err in enumerate(classified, 1):
            lines.append(f"Error {i}: {err.category.value}")
            lines.append(f"  Recoverable: {err.recoverable}")
            lines.append(f"  Sugerencia: {err.suggestion}")
            if err.raw_error:
                preview = err.raw_error[:200]
                lines.append(f"  Detalle: {preview}...")
            lines.append("")

        return "\n".join(lines)
