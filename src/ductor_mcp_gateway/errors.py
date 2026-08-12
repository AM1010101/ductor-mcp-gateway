"""Errors raised by the typed Ductor API client."""

from __future__ import annotations


class DuctorError(Exception):
    """Base class for safe-to-report Ductor client errors."""


class DuctorUnavailableError(DuctorError):
    """The Ductor service could not be reached in time."""


class DuctorUpstreamError(DuctorError):
    """Ductor returned a non-successful HTTP status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Ductor upstream returned HTTP {status_code}")


class DuctorProtocolError(DuctorError):
    """Ductor returned an oversized or schema-invalid response."""
