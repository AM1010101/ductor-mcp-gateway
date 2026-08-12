"""Structured audit events that intentionally exclude secrets and prompt bodies."""

from __future__ import annotations

import json
import logging
import time
from typing import Any


class AuditLogger:
    """Emit minimal JSON audit records through the standard logging pipeline."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("ductor_mcp_gateway.audit")

    def event(self, name: str, **fields: str | int | float | bool | None) -> None:
        record: dict[str, Any] = {"event": name, "time_unix": round(time.time(), 3)}
        record.update({key: value for key, value in fields.items() if value is not None})
        self._logger.info(json.dumps(record, sort_keys=True, separators=(",", ":")))
