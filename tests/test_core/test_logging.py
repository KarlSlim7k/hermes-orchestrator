"""Tests para logging.py (T-06bis)."""

import json
import logging
import tempfile
import os
from pathlib import Path

import pytest

from src.core.logging import (
    setup_logging,
    get_logger,
    get_task_logger,
    log_event,
    JsonFormatter,
    ColoredFormatter,
)


# ---------------------------------------------------------------------------
# Setup logging
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def teardown_method(self):
        # Clean up root logger after each test
        root = logging.getLogger("hermes")
        root.handlers.clear()

    def test_setup_creates_console_handler(self):
        logger = setup_logging(level="INFO", log_file=None)
        assert len(logger.handlers) >= 1
        assert any(
            isinstance(h, logging.StreamHandler) for h in logger.handlers
        )

    def test_setup_creates_file_handler(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        tmp.close()
        try:
            logger = setup_logging(level="DEBUG", log_file=tmp.name)
            file_handlers = [
                h for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers) == 1
        finally:
            os.unlink(tmp.name)

    def test_setup_with_json_format(self):
        logger = setup_logging(level="INFO", json_format=True)
        console_handler = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ][0]
        assert isinstance(console_handler.formatter, JsonFormatter)

    def test_setup_silences_noisy_loggers(self):
        setup_logging(level="INFO")
        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_setup_module_level_overrides(self):
        setup_logging(
            level="INFO",
            module_levels={"hermes.debug_mod": "DEBUG"},
        )
        mod_logger = logging.getLogger("hermes.debug_mod")
        assert mod_logger.level == logging.DEBUG

    def test_clears_previous_handlers(self):
        """Multiple calls should not duplicate handlers."""
        setup_logging(level="INFO", log_file=None)
        handler_count = len(logging.getLogger("hermes").handlers)
        setup_logging(level="INFO", log_file=None)
        assert len(logging.getLogger("hermes").handlers) == handler_count


# ---------------------------------------------------------------------------
# ColoredFormatter
# ---------------------------------------------------------------------------

class TestColoredFormatter:
    def test_formats_log_record(self):
        fmt = ColoredFormatter("%(levelname)s - %(message)s", use_color=False)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        output = fmt.format(record)
        assert "INFO" in output
        assert "hello" in output

    def test_color_codes_in_output(self):
        fmt = ColoredFormatter("%(levelname)s - %(message)s", use_color=True)
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="watch out", args=(), exc_info=None,
        )
        output = fmt.format(record)
        assert "\033[33m" in output  # yellow for warning
        assert "\033[0m" in output   # reset


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------

class TestJsonFormatter:
    def test_produces_valid_json(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="hermes.test", level=logging.INFO, pathname="", lineno=42,
            msg="test message", args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "test message"
        assert data["logger"] == "hermes.test"
        assert data["line"] == 42

    def test_includes_exception_info(self):
        import sys
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="error occurred", args=(), exc_info=sys.exc_info(),
            )
            output = fmt.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------

class TestGetLogger:
    def test_returns_hermes_child(self):
        logger = get_logger("orchestrator.router")
        assert logger.name == "hermes.orchestrator.router"

    def test_logs_propagate_to_root(self):
        setup_logging(level="INFO", log_file=None)
        logger = get_logger("test_mod")
        # Should not raise
        logger.info("test message")


# ---------------------------------------------------------------------------
# get_task_logger
# ---------------------------------------------------------------------------

class TestGetTaskLogger:
    def test_returns_logger_adapter(self):
        adapter = get_task_logger("abc-123-def-456")
        assert isinstance(adapter, logging.LoggerAdapter)
        assert adapter.extra["task_id"] == "abc-123-def-456"

    def test_logger_name_is_truncated(self):
        adapter = get_task_logger("very-long-task-id-12345")
        # El task_id se trunca a 8 chars en el nombre del logger
        assert "very-lon" in adapter.logger.name


# ---------------------------------------------------------------------------
# log_event helper
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_logs_event_with_task_id(self, caplog):
        setup_logging(level="INFO", log_file=None)
        logger = get_logger("test_events")
        log_event(logger, "task-123", "started", "initializing")
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "task-123" in record.message
        assert "started" in record.message
        assert "initializing" in record.message

    def test_logs_event_without_details(self, caplog):
        setup_logging(level="INFO", log_file=None)
        logger = get_logger("test_events2")
        log_event(logger, "task-456", "completed")
        assert len(caplog.records) == 1
        assert "task-456" in caplog.records[0].message
        assert "—" not in caplog.records[0].message


# ---------------------------------------------------------------------------
# File log output
# ---------------------------------------------------------------------------

class TestFileLogging:
    def teardown_method(self):
        root = logging.getLogger("hermes")
        root.handlers.clear()

    def test_writes_to_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        tmp.close()
        try:
            logger = setup_logging(level="DEBUG", log_file=tmp.name)
            logger.info("test write to file")

            # Flush handlers
            for h in logger.handlers:
                h.flush()

            content = Path(tmp.name).read_text()
            assert "test write to file" in content
        finally:
            os.unlink(tmp.name)

    def test_json_format_writes_valid_json(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        tmp.close()
        try:
            logger = setup_logging(level="INFO", log_file=tmp.name, json_format=True)
            logger.info("json test")
            for h in logger.handlers:
                h.flush()

            content = Path(tmp.name).read_text().strip()
            # Each line should be valid JSON
            for line in content.split("\n"):
                if line.strip():
                    data = json.loads(line)
                    assert "level" in data
                    assert "message" in data
        finally:
            os.unlink(tmp.name)
