"""Process-level logging configuration for Discord and local runs."""
from __future__ import annotations

import atexit
import json
import logging
import queue
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any

from app import config, observability
from app.observability import current_context

_listener: QueueListener | None = None
_listener_targets: list[logging.Handler] = []


class _DaemonQueueListener(QueueListener):
    def start(self) -> None:
        self._thread = threading.Thread(target=self._monitor, daemon=True)  # type: ignore[attr-defined]
        self._thread.start()


def _stop_listener() -> None:
    global _listener, _listener_targets
    if _listener is not None:
        _listener.stop()
        _listener = None
    for handler in _listener_targets:
        handler.close()
    _listener_targets = []

class _ChannelFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "structured_event", None) is not None:
            return config.LOG_ENABLED
        return config.LOG_TEXT_ENABLED


def _base_record(record: logging.LogRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": record.levelname,
        "logger": record.name,
    }
    result.update(current_context())
    return result


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = _base_record(record)
        structured = getattr(record, "structured_event", None)
        if structured is not None:
            result.update(structured)
        else:
            result["event"] = "log.message"
            result["message"] = record.getMessage()
        if record.exc_info:
            result["exception"] = self.formatException(record.exc_info)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = _base_record(record)
        structured = getattr(record, "structured_event", None)
        if structured is not None:
            result.update(structured)
            message = structured.get("event", record.getMessage())
        else:
            message = record.getMessage()
        context_values = " ".join(
            f"{key}={value}" for key, value in result.items()
            if key not in {"timestamp", "level", "logger", "event", "message"}
        )
        suffix = f" {context_values}" if context_values else ""
        output = f"{result['timestamp']} {result['level']} {result['logger']} {message}{suffix}"
        if record.exc_info:
            output += "\n" + self.formatException(record.exc_info)
        return output


def configure_logging(*, force: bool = False) -> None:
    """Install one filtered handler for the process.

    ``force`` is useful for tests or a deliberate application reconfigure;
    normal startup should call this once before connecting to Discord.
    """
    root = logging.getLogger()
    if force:
        _stop_listener()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()

    if not config.LOG_ENABLED and not config.LOG_TEXT_ENABLED:
        return

    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    formatter: logging.Formatter = (
        StructuredFormatter() if config.LOG_FORMAT == "json" else TextFormatter()
    )
    channel_filter = _ChannelFilter()

    if root.handlers and not force:
        # A host application may have configured logging before the bot. Reuse
        # those handlers instead of silently skipping the channel toggles.
        for handler in root.handlers:
            handler.setFormatter(formatter)
            handler.addFilter(channel_filter)
        for setting, received_kind, fallback_value in config.INVALID_LOG_SETTINGS:
            observability.event(
                "config.invalid", level=logging.WARNING, setting=setting,
                received_kind=received_kind, fallback_value=fallback_value,
            )
        config.INVALID_LOG_SETTINGS.clear()
        return

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.addFilter(channel_filter)
    targets: list[logging.Handler] = [stream]

    if config.LOG_FILE:
        try:
            path = Path(config.LOG_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(channel_filter)
            targets.append(file_handler)
        except OSError:
            # Keep stderr alive; logging must not prevent the bot from starting.
            root.warning("log_file_setup_failed path=%s", config.LOG_FILE)

    global _listener, _listener_targets
    _listener_targets = targets
    log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    root.addHandler(QueueHandler(log_queue))
    _listener = _DaemonQueueListener(log_queue, *targets, respect_handler_level=False)
    _listener.start()

    for setting, received_kind, fallback_value in config.INVALID_LOG_SETTINGS:
        observability.event(
            "config.invalid", level=logging.WARNING, setting=setting,
            received_kind=received_kind, fallback_value=fallback_value,
        )
    config.INVALID_LOG_SETTINGS.clear()


atexit.register(_stop_listener)
