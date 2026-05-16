"""Tests para T-06 (base agent) y T-08 (OpenCode adapter)."""

import json
import pytest

from src.agents.base import BaseAgent, AgentResult, AgentProgress, AgentStreamEvent
from src.agents.opencode_adapter import OpenCodeAdapter


# --- Helpers ---


class ConcreteAgent(BaseAgent):
    """Implementacion concreta minima para tests de la clase base."""

    def build_command(self, prompt, workdir=None, extra_args=None):
        cmd = ["echo", prompt]
        if workdir:
            cmd.extend(["--dir", workdir])
        if extra_args:
            cmd.extend(extra_args)
        return cmd

    def parse_result(self, stdout, stderr, exit_code):
        return AgentResult(
            status="completed" if exit_code == 0 else "failed",
            summary=stdout.strip(),
            raw_output=stdout,
            exit_code=exit_code,
        )

    def parse_progress(self, line):
        return super().parse_progress(line)


class ShellAgent(BaseAgent):
    """Agente concreto que ejecuta comandos de shell reales (para tests)."""

    def build_command(self, prompt, workdir=None, extra_args=None):
        cmd = ["bash", "-c", prompt]
        if extra_args:
            cmd.extend(extra_args)
        return cmd

    def parse_result(self, stdout, stderr, exit_code):
        return AgentResult(
            status="completed" if exit_code == 0 else "failed",
            summary=stdout.strip(),
            raw_output=stdout,
            exit_code=exit_code,
        )


# --- BaseAgent tests ---


class TestBaseAgentBuildCommand:
    def test_build_command_basic(self):
        agent = ConcreteAgent(cli_command="echo")
        cmd = agent.build_command("hello world")
        assert cmd == ["echo", "hello world"]

    def test_build_command_with_workdir(self):
        agent = ConcreteAgent(cli_command="echo")
        cmd = agent.build_command("hello", workdir="/tmp")
        assert "--dir" in cmd
        assert "/tmp" in cmd

    def test_build_command_with_extra_args(self):
        agent = ConcreteAgent(cli_command="echo")
        cmd = agent.build_command("hello", extra_args=["--verbose", "--json"])
        assert "--verbose" in cmd
        assert "--json" in cmd


class TestBaseAgentRunSync:
    def test_run_sync_success(self):
        agent = ConcreteAgent(cli_command="echo")
        result = agent.run_sync("test output")
        assert result.status == "completed"
        assert "test output" in result.summary

    def test_run_sync_timeout(self):
        agent = ShellAgent(cli_command="bash", timeout_seconds=1)
        result = agent.run_sync("sleep 10")
        assert result.status == "failed"
        assert result.exit_code == -1  # timeout returns -1

    def test_run_sync_with_workdir(self):
        agent = ConcreteAgent(cli_command="pwd")
        result = agent.run_sync("", workdir="/tmp")
        assert result.status == "completed"


class TestBaseAgentParseProgress:
    def test_parse_progress_standard_format(self):
        agent = ConcreteAgent(cli_command="echo")
        # Formato estandar [PROGRESS] <pct> - <msg>
        progress = agent.parse_progress("[PROGRESS] 50 - Compilando modulos")
        assert progress is not None
        assert progress.percentage == 50.0
        assert progress.message == "Compilando modulos"

    def test_parse_progress_colon_separator(self):
        agent = ConcreteAgent(cli_command="echo")
        progress = agent.parse_progress("[PROGRESS] 75.5: Analisis completado")
        assert progress is not None
        assert progress.percentage == 75.5
        assert progress.message == "Analisis completado"

    def test_parse_progress_no_match(self):
        agent = ConcreteAgent(cli_command="echo")
        assert agent.parse_progress("texto normal sin progreso") is None

    def test_parse_progress_invalid_percentage(self):
        agent = ConcreteAgent(cli_command="echo")
        assert agent.parse_progress("[PROGRESS] abc - algo") is None


class TestBaseAgentStreamEvents:
    def test_stream_yields_events(self):
        agent = ShellAgent(cli_command="bash")
        events = list(agent.stream_events(
            "echo 'linea1'; echo '[PROGRESS] 30 - avance'; echo 'linea2'"
        ))
        assert len(events) > 0
        event_types = [e.event_type for e in events]
        assert "progress" in event_types


class TestBaseAgentRepr:
    def test_repr(self):
        agent = ConcreteAgent(cli_command="test-cli", workdir="/home/user")
        assert "ConcreteAgent" in repr(agent)
        assert "test-cli" in repr(agent)


# --- OpenCodeAdapter tests ---


SAMPLE_OPENCODE_OUTPUT = json.dumps({
    "type": "step_start",
    "timestamp": 1778966869264,
    "sessionID": "ses_abc123",
    "part": {"id": "prt_1", "type": "step-start"},
}) + "\n" + json.dumps({
    "type": "text",
    "timestamp": 1778966869841,
    "sessionID": "ses_abc123",
    "part": {"id": "prt_2", "type": "text", "text": "He creado los archivos src/auth.py y tests/test_auth.py"},
}) + "\n" + json.dumps({
    "type": "step_finish",
    "timestamp": 1778966870086,
    "sessionID": "ses_abc123",
    "part": {
        "id": "prt_3",
        "reason": "stop",
        "type": "step-finish",
        "tokens": {"total": 10844, "input": 6, "output": 33, "reasoning": 0, "cache": {"write": 10805, "read": 0}},
        "cost": 0.006855125,
    },
})


class TestOpenCodeAdapterBuildCommand:
    @pytest.fixture
    def adapter(self):
        return OpenCodeAdapter(
            cli_command="/usr/bin/opencode",
            workdir="/home/user/project",
            auto_approve=True,
        )

    def test_basic_command(self, adapter):
        cmd = adapter.build_command("analiza el codigo")
        assert cmd[0] == "/usr/bin/opencode"
        assert cmd[1] == "run"
        assert "analiza el codigo" in cmd
        assert "--format" in cmd
        assert "json" in cmd

    def test_auto_approve_flag(self, adapter):
        cmd = adapter.build_command("haz algo")
        assert "--dangerously-skip-permissions" in cmd

    def test_no_auto_approve(self):
        adapter = OpenCodeAdapter(cli_command="opencode", auto_approve=False)
        cmd = adapter.build_command("haz algo")
        assert "--dangerously-skip-permissions" not in cmd

    def test_workdir_override(self, adapter):
        cmd = adapter.build_command("test", workdir="/other/dir")
        assert "--dir" in cmd
        assert "/other/dir" in cmd

    def test_extra_args(self, adapter):
        cmd = adapter.build_command("test", extra_args=["--model", "gpt-4"])
        assert "--model" in cmd
        assert "gpt-4" in cmd


class TestOpenCodeAdapterParseResult:
    @pytest.fixture
    def adapter(self):
        return OpenCodeAdapter(cli_command="opencode")

    def test_parse_success(self, adapter):
        result = adapter.parse_result(SAMPLE_OPENCODE_OUTPUT, "", 0)
        assert result.status == "completed"
        assert result.session_id == "ses_abc123"
        assert result.tokens_total == 10844
        assert result.cost == 0.006855125
        assert "auth.py" in result.summary

    def test_parse_error_status(self, adapter):
        result = adapter.parse_result("", "error interno", 1)
        assert result.status == "failed"
        assert result.exit_code == 1

    def test_parse_step_finish_error(self, adapter):
        error_output = json.dumps({
            "type": "step_finish",
            "sessionID": "ses_err",
            "part": {"reason": "error", "type": "step-finish"},
        })
        result = adapter.parse_result(error_output, "", 0)
        assert result.status == "failed"
        assert len(result.errors) > 0

    def test_parse_explicit_error_event(self, adapter):
        error_output = json.dumps({
            "type": "error",
            "part": {"message": "Rate limit exceeded"},
        })
        result = adapter.parse_result(error_output, "", 0)
        assert result.status == "failed"
        assert "Rate limit" in str(result.errors)

    def test_parse_mixed_json_and_text(self, adapter):
        mixed = "Some plain text\n" + SAMPLE_OPENCODE_OUTPUT + "\nMore text"
        result = adapter.parse_result(mixed, "", 0)
        assert result.status == "completed"

    def test_empty_output(self, adapter):
        result = adapter.parse_result("", "", 0)
        assert result.status == "completed"
        assert "exited with code 0" in result.summary

    def test_summary_truncated(self, adapter):
        long_text = json.dumps({
            "type": "text",
            "part": {"type": "text", "text": "A" * 500},
        })
        result = adapter.parse_result(long_text, "", 0)
        assert len(result.summary) <= 200

    def test_stderr_captured_in_details(self, adapter):
        result = adapter.parse_result("", "stderr output here", 0)
        assert "stderr" in result.details
        assert result.details["stderr"] == "stderr output here"
