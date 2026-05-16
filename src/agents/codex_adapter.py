"""Adaptador para Codex CLI (T-07).

Invoca `codex exec` con flag --json para parsear eventos JSONL
y capturar el resultado estructurado.
"""

import json
import shlex
from typing import Optional

from src.agents.base import BaseAgent, AgentResult, AgentProgress


# Patrones para detectar archivos modificados en la salida de texto.
_FILE_CHANGED_PATTERNS = [
    r"Written to `(.+?)`",
    r"edited `(.+?)`",
    r"created `(.+?)`",
    r"modified `(.+?)`",
    r"Applied diff to `(.+?)`",
]


class CodexAdapter(BaseAgent):
    """Adaptador para Codex CLI.

    Usa `codex exec --json` para ejecucion no interactiva con
    salida JSONL parseable. Soporta sandbox y bypass de aprobaciones.
    """

    def build_command(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> list[str]:
        cmd = [self.cli_command, "exec", "--json"]

        effective_dir = workdir or self.workdir
        if effective_dir:
            cmd.extend(["-C", effective_dir])

        if self.auto_approve:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")

        cmd.append(prompt)

        if extra_args:
            cmd.extend(extra_args)

        return cmd

    def parse_result(self, stdout: str, stderr: str, exit_code: int) -> AgentResult:
        """Parsear salida JSONL de codex exec en un AgentResult.

        El formato JSONL de codex emite una linea JSON por evento.
        Cada evento tiene un tipo (type) y datos asociados.
        """
        collected_text: list[str] = []
        files_modified: list[str] = []
        errors: list[str] = []
        session_id: Optional[str] = None

        for raw_line in stdout.strip().splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                collected_text.append(raw_line)
                continue

            event_type = event.get("type", "")

            if event_type == "subagent_status":
                # Status updates durante la ejecucion.
                pass
            elif event_type == "prompt":
                pass
            elif event_type == "error":
                error_msg = event.get("message", event.get("error", raw_line))
                errors.append(error_msg)
            else:
                # Guardar texto de eventos no reconocidos especificamente.
                msg = event.get("message", event.get("text", ""))
                if msg:
                    collected_text.append(msg)

        # Extraer archivos modificados del texto recopilado.
        import re
        all_text = "\n".join(collected_text)
        for pattern in _FILE_CHANGED_PATTERNS:
            files_modified.extend(re.findall(pattern, all_text))
        files_modified = list(dict.fromkeys(files_modified))

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
            details={"stderr": stderr} if stderr else {},
        )

    @staticmethod
    def _build_summary(collected_text: list[str], exit_code: int) -> str:
        if not collected_text:
            return f"Agent exited with code {exit_code}"
        text = "\n".join(collected_text).strip()
        if len(text) <= 200:
            return text
        return text[:197] + "..."
