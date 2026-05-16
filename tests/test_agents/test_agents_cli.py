"""Tests para T-07 (Codex adapter), T-09 (ProgressTracker), T-10 (ErrorHandler)."""

import json
import pytest

from src.agents.codex_adapter import CodexAdapter
from src.agents.progress_tracker import ProgressTracker, ProgressState
from src.agents.error_handler import ErrorHandler, ErrorCategory, AgentError
from src.agents.base import AgentResult, AgentStreamEvent


# ====== T-07: CodexAdapter ======


SAMPLE_CODEX_JSONL = "\n".join([
    json.dumps({"type": "prompt", "message": "analyzing codebase"}),
    json.dumps({"type": "subagent_status", "status": "running"}),
    json.dumps({"type": "message", "message": "Created src/auth.py and tests/test_auth.py"}),
])


class TestCodexAdapterBuildCommand:
    @pytest.fixture
    def adapter(self):
        return CodexAdapter(
            cli_command="/usr/bin/codex",
            workdir="/home/user/project",
            auto_approve=True,
        )

    def test_basic_command(self, adapter):
        cmd = adapter.build_command("analiza el codigo")
        assert cmd[0] == "/usr/bin/codex"
        assert cmd[1] == "exec"
        assert "--json" in cmd
        assert "analiza el codigo" in cmd

    def test_auto_approve_flag(self, adapter):
        cmd = adapter.build_command("haz algo")
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_no_auto_approve(self):
        adapter = CodexAdapter(cli_command="codex", auto_approve=False)
        cmd = adapter.build_command("haz algo")
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_workdir_override(self, adapter):
        cmd = adapter.build_command("test", workdir="/other/dir")
        assert "-C" in cmd
        assert "/other/dir" in cmd

    def test_extra_args(self, adapter):
        cmd = adapter.build_command("test", extra_args=["--model", "o3"])
        assert "--model" in cmd
        assert "o3" in cmd


class TestCodexAdapterParseResult:
    @pytest.fixture
    def adapter(self):
        return CodexAdapter(cli_command="codex")

    def test_parse_success(self, adapter):
        result = adapter.parse_result(SAMPLE_CODEX_JSONL, "", 0)
        assert result.status == "completed"
        assert "auth.py" in result.summary

    def test_parse_error_status(self, adapter):
        result = adapter.parse_result("", "error interno", 1)
        assert result.status == "failed"
        assert result.exit_code == 1

    def test_parse_json_error_event(self, adapter):
        error_output = json.dumps({
            "type": "error",
            "message": "Permission denied: sandbox restriction",
        })
        result = adapter.parse_result(error_output, "", 1)
        assert result.status == "failed"
        assert len(result.errors) > 0
        assert "Permission denied" in result.errors[0]

    def test_parse_mixed_json_and_text(self, adapter):
        mixed = "Some plain text\n" + SAMPLE_CODEX_JSONL + "\nMore text"
        result = adapter.parse_result(mixed, "", 0)
        assert result.status == "completed"

    def test_empty_output(self, adapter):
        result = adapter.parse_result("", "", 0)
        assert result.status == "completed"
        assert "exited with code 0" in result.summary

    def test_summary_truncated(self, adapter):
        long_text = json.dumps({
            "type": "message",
            "message": "A" * 500,
        })
        result = adapter.parse_result(long_text, "", 0)
        assert len(result.summary) <= 200

    def test_stderr_captured_in_details(self, adapter):
        result = adapter.parse_result("", "stderr output", 0)
        assert "stderr" in result.details
        assert result.details["stderr"] == "stderr output"


# ====== T-09: ProgressTracker ======


class TestProgressTracker:
    def test_initial_state(self):
        tracker = ProgressTracker()
        assert tracker.state.percentage == 0.0
        assert tracker.state.current_step == ""
        assert tracker.state.start_time is None

    def test_update_progress_event(self):
        tracker = ProgressTracker()
        event = AgentStreamEvent(
            event_type="progress",
            data={"percentage": 50.0, "message": "Analizando modulos"},
        )
        changed = tracker.update(event)
        assert changed is True
        assert tracker.state.percentage == 50.0
        assert tracker.state.current_step == "Analizando modulos"
        assert tracker.state.start_time is not None

    def test_update_stdout_line_detects_step(self):
        tracker = ProgressTracker()
        # First event to initialize start_time.
        tracker.update(AgentStreamEvent(
            event_type="stdout_line",
            data={"line": "starting work"},
        ))
        event = AgentStreamEvent(
            event_type="stdout_line",
            data={"line": "compilando modulo auth"},
        )
        changed = tracker.update(event)
        assert changed is True
        assert "compilando" in tracker.state.current_step.lower()

    def test_update_step_finish_sets_100(self):
        tracker = ProgressTracker()
        tracker.update(AgentStreamEvent(
            event_type="step_finish",
            data={"reason": "stop"},
        ))
        assert tracker.state.percentage == 100.0

    def test_eta_calculation(self):
        tracker = ProgressTracker()
        tracker.update(AgentStreamEvent(
            event_type="progress",
            data={"percentage": 50.0, "message": "halfway"},
        ))
        # After 50% with some elapsed time, ETA should be calculable.
        assert tracker.state.eta_seconds is not None
        assert tracker.state.eta_human != "desconocido"

    def test_eta_none_at_zero_percent(self):
        tracker = ProgressTracker()
        # Force start_time without progress.
        tracker._started = True
        tracker.state.start_time = 0.0
        assert tracker.state.percentage == 0.0
        assert tracker.state.eta_seconds is None

    def test_reset(self):
        tracker = ProgressTracker()
        tracker.update(AgentStreamEvent(
            event_type="progress",
            data={"percentage": 30, "message": "test"},
        ))
        tracker.reset()
        assert tracker.state.percentage == 0.0
        assert tracker.state.current_step == ""
        assert tracker._started is False


# ====== T-10: ErrorHandler ======


class TestErrorHandlerClassify:
    def test_no_errors_on_completed(self):
        result = AgentResult(status="completed", summary="done")
        errors = ErrorHandler.classify(result)
        assert len(errors) == 0

    def test_classify_timeout(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="the request timed out",
            exit_code=-1,
        )
        errors = ErrorHandler.classify(result)
        assert len(errors) >= 1
        assert errors[0].category == ErrorCategory.TIMEOUT
        assert errors[0].recoverable is True

    def test_classify_permission_denied(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="permission denied: cannot access file",
            exit_code=1,
        )
        errors = ErrorHandler.classify(result)
        assert any(e.category == ErrorCategory.PERMISSION_DENIED for e in errors)

    def test_classify_agent_crash(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="segmentation fault (core dumped)",
            exit_code=139,
        )
        errors = ErrorHandler.classify(result)
        assert any(e.category == ErrorCategory.AGENT_CRASH for e in errors)
        assert all(not e.recoverable for e in errors if e.category == ErrorCategory.AGENT_CRASH)

    def test_classify_rate_limit(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="error 429: too many requests, rate limit exceeded",
            exit_code=1,
        )
        errors = ErrorHandler.classify(result)
        assert any(e.category == ErrorCategory.RATE_LIMIT for e in errors)

    def test_classify_out_of_context(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="context length exceeded: token limit reached",
            exit_code=1,
        )
        errors = ErrorHandler.classify(result)
        assert any(e.category == ErrorCategory.OUT_OF_CONTEXT for e in errors)

    def test_classify_unknown_error(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="something weird happened",
            exit_code=42,
        )
        errors = ErrorHandler.classify(result)
        assert any(e.category == ErrorCategory.UNKNOWN for e in errors)

    def test_classify_from_stderr(self):
        result = AgentResult(
            status="failed",
            summary="failed",
            raw_output="",
            details={"stderr": "permission denied: access forbidden"},
            exit_code=1,
        )
        errors = ErrorHandler.classify(result)
        assert any(e.category == ErrorCategory.PERMISSION_DENIED for e in errors)


class TestErrorHandlerIsRecoverable:
    def test_recoverable_timeout(self):
        result = AgentResult(
            status="failed", summary="", raw_output="timed out", exit_code=-1
        )
        assert ErrorHandler.is_recoverable(result) is True

    def test_not_recoverable_crash(self):
        result = AgentResult(
            status="failed", summary="", raw_output="segmentation fault", exit_code=139
        )
        assert ErrorHandler.is_recoverable(result) is False

    def test_completed_not_recoverable(self):
        result = AgentResult(status="completed", summary="ok")
        assert ErrorHandler.is_recoverable(result) is False


class TestErrorHandlerBuildReport:
    def test_completed_report(self):
        result = AgentResult(status="completed", summary="done")
        report = ErrorHandler.build_report(result)
        assert "Sin errores" in report

    def test_failed_report_contains_errors(self):
        result = AgentResult(
            status="failed",
            summary="",
            raw_output="permission denied: sandbox restriction",
            exit_code=1,
        )
        report = ErrorHandler.build_report(result)
        assert "permission_denied" in report
        assert "Recoverable" in report
        assert "Sugerencia" in report
