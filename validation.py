"""Bounded public input validation."""

from __future__ import annotations
import json
import math
from typing import Any


class Failure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.uncertain = False


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Failure("invalid_payload", message)


def integer(value: Any, name: str, low: int, high: int) -> int:
    require(
        type(value) is int and low <= value <= high,
        f"{name} must be an integer from {low} to {high}.",
    )
    return value


def vector(value: Any) -> list[float]:
    require(
        isinstance(value, list) and 1 <= len(value) <= 65536,
        "A bounded dense vector is required.",
    )
    require(
        all(type(v) in (float, int) and math.isfinite(v) for v in value),
        "Vector values must be finite numbers.",
    )
    return value


def bounded_json(value: Any, limit: int = 65536) -> Any:
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
        require(len(encoded.encode()) <= limit, "JSON value exceeds the byte limit.")
        return json.loads(encoded)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise Failure("invalid_payload", "A bounded JSON value is required.") from exc


def user_filter(value: Any) -> dict:
    require(isinstance(value, dict), "Filter must be an object.")
    return bounded_json(value)


def identifier(value: Any) -> str:
    require(
        type(value) in (str, int) and not isinstance(value, bool),
        "Point ID must be a string or integer.",
    )
    require(
        bool(str(value).strip()) and len(str(value).encode()) <= 256,
        "Point ID must be bounded and nonempty.",
    )
    return str(value)
