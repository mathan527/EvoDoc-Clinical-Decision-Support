from __future__ import annotations

from typing import Any


def build_error_response(
    *,
    error_code: str,
    category: str,
    message: str,
    request_id: str | None,
    details: Any | None = None,
    recoverable: bool = True,
) -> dict[str, Any]:
    return {
        "error": {
            "error_code": error_code,
            "category": category,
            "message": message,
            "recoverable": recoverable,
            "details": details,
        },
        "request_id": request_id,
    }
