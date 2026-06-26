"""Tests de mavod.logging_setup."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from mavod.logging_setup import (
    _HumanFormatter,
    _JsonFormatter,
    configure_logging,
    get_logger,
    reset_for_tests,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    yield
    reset_for_tests()


def test_get_logger_returns_logger():
    """get_logger retourne un logger configuré."""
    log = get_logger("test.module")
    assert log.name == "test.module"


def test_json_formatter_emits_event_field():
    """Le formateur JSON émet bien le champ event."""
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg="workflow.search.done", args=(), exc_info=None,
    )
    record.search_id = "abc"
    record.candidates = 7
    out = _JsonFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["event"] == "workflow.search.done"
    assert parsed["search_id"] == "abc"
    assert parsed["candidates"] == 7
    assert parsed["level"] == "INFO"


def test_human_formatter_includes_extras():
    """Le formateur human inclut les extras."""
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg="step.done", args=(), exc_info=None,
    )
    record.search_id = "abc"
    out = _HumanFormatter().format(record)
    assert "step.done" in out
    assert "search_id='abc'" in out


def test_configure_logging_idempotent():
    """configure_logging est idempotent."""
    configure_logging()
    handlers_first = list(logging.getLogger().handlers)
    configure_logging()  # second call no-op
    handlers_second = list(logging.getLogger().handlers)
    assert len(handlers_first) == len(handlers_second)


def test_configure_logging_with_file(tmp_path):
    """configure_logging écrit dans un fichier."""
    log_file = tmp_path / "logs" / "bot.log"
    configure_logging(json_output=False, log_file=log_file)
    log = get_logger("test.file")
    log.info("hello.file")
    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()
    assert log_file.exists()
    content = log_file.read_text()
    assert "hello.file" in content
