"""Adaptador para OpenCode CLI (T-08).

Invoca `opencode run` con formato JSON para parsear eventos
de salida estructurados (step_start, text, step_finish).
"""

import json
import shlex
from typing import Optional

from src.agents.base import BaseAgent, AgentResult


# Patrones para detectar archivos modificados en la salida de texto.
_FILE_CHANGED_PATTERNS = [
    r"Written to `(.+?)`",
    r"edited `(.+?)`",
    r"created `(.+?)`",
    r"modified `(.+?)`",
]


class OpenCodeAdapter(BaseAgent):
    """Adaptador para OpenCode CLI.

    Usa `opencode run --format json --dangerously-skip-permissions`
    para ejecucion no interactiva con salida JSON parseable.
    """

    PROGRESS_EVENTS = ("step_start", "step_finish", "text")

    def build_command(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> list[str]:
        cmd = [self.cli_command, "run", prompt, "--format", "json"]

        effective_dir = workdir or self.workdir
        if effective_dir:
            cmd.extend(["--dir", effective_dir])

        if self.auto_approve:
            cmd.append("--dangerously-skip-permissions")

        if extra_args:
            cmd.extend(extra_args)

        return cmd

    def parse_result(self, stdout: str, stderr: str, exit_code: int) -> AgentResult:
        """Parsear salida JSON de opencode en un AgentResult.

        El formato JSON de opencode emite una linea JSON por evento:
        - step_start: inicio de paso
        - text: texto generado por el agente
        - step_finish: fin de paso con tokens y costo
        """
        collected_text: list[str] = []
        files_modified: list[str] = []
        session_id: Optional[str] = None
        tokens_total: Optional[int] = None
        cost: Optional[float] = None
        errors: list[str] = []

        # Parsear eventos JSON linea por linea.
        for raw_line in stdout.strip().splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                # Lineas que no son JSON se tratan como texto plano.
                collected_text.append(raw_line)
                continue

            event_type = event.get("type", "")
            part = event.get("part", {})

            if event_type == "step_start":
                session_id = event.get("sessionID")

            elif event_type == "text":
                text = part.get("text", "")
                if text:
                    collected_text.append(text)

            elif event_type == "step_finish":
                reason = part.get("reason", "")
                token_info = part.get("tokens", {})
                tokens_total = token_info.get("total")
                cost = part.get("cost")

                if reason == "error":
                    errors.append("Agent step finished with error reason")

            elif event_type == "error":
                errors.append(part.get("message", raw_line))

        # Extraer archivos modificados del texto recopilado.
        import re
        all_text = "\n".join(collected_text)
        for pattern in _FILE_CHANGED_PATTERNS:
            files_modified.extend(re.findall(pattern, all_text))
        files_modified = list(dict.fromkeys(files_modified))  # dedup preserving order

        full_output = stdout if stdout else stderr
        summary = self._build_summary(collected_text, exit_code)

        status = "completed" if exit_code == 0 else "failed"
        if errors:
            status = "failed"

        return AgentResult(
            status=status,
            summary=summary,
            files_modified=files_modified,
            errors=errors,
            raw_output=full_output,
            exit_code=exit_code,
            session_id=session_id,
            tokens_total=tokens_total,
            cost=cost,
            details={"stderr": stderr} if stderr else {},
        )

    @staticmethod
    def _build_summary(collected_text: list[str], exit_code: int) -> str:
        """Construir un resumen breve de la salida del agente."""
        if not collected_text:
            return f"Agent exited with code {exit_code}"

        # Tomar las primeras lineas como resumen (max ~200 chars).
        text = "\n".join(collected_text).strip()
        if len(text) <= 200:
            return text
        return text[:197] + "..."
