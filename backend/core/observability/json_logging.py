"""Structured JSON logs (OBS-1..3): one JSON object per line, always with the request id."""

import json
import logging
from datetime import UTC, datetime

from core.observability import context

# Attributes every LogRecord has; anything else was passed through `extra=`.
_STANDARD_ATTRIBUTES = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = context.current().request_id
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": str(request_id) if request_id else None,
        }
        payload.update(
            {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
