"""Pinned, bounded, non-retrying Qdrant HTTP transport."""

from __future__ import annotations
import ipaddress
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
from flowsteward_extension_sdk import open_pinned_url, resolve_pinned_ips
from validation import Failure, bounded_json


class Transport:
    def __init__(self, config: dict, secrets: dict):
        self.base = config.get("base_url", "").rstrip("/")
        parsed = urlsplit(self.base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or any(ord(c) < 33 for c in self.base)
        ):
            raise Failure(
                "invalid_connection",
                "Use an HTTP(S) origin without credentials, path, query or fragment.",
            )
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.scheme = parsed.scheme
        self.timeout = config.get("request_timeout_seconds", 30)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 60:
            raise Failure("invalid_connection", "Request timeout must be 1 to 60 seconds.")
        self.key = secrets.get("api_key", "")
        if (
            not isinstance(self.key, str)
            or len(self.key) > 8192
            or any(ord(c) < 32 for c in self.key)
        ):
            raise Failure("invalid_connection", "Invalid API key.")
        self.ips: tuple[str, ...] | None = None

    def request(self, method: str, path: str, body: dict | None = None):
        if self.ips is None:
            try:
                self.ips = tuple(
                    resolve_pinned_ips(self.host, port=self.port, purpose="Qdrant endpoint")
                )
                private = all(not ipaddress.ip_address(ip).is_global for ip in self.ips)
                allow = os.environ.get("FS_ALLOW_PRIVATE_REMOTE_URLS", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                if (
                    not self.ips
                    or (private and not allow)
                    or (self.scheme == "http" and not private)
                ):
                    raise ValueError("Blocked endpoint")
            except (ValueError, OSError) as exc:
                raise Failure(
                    "network_blocked",
                    "Qdrant endpoint is blocked by host network policy.",
                ) from exc
        encoded = (
            None
            if body is None
            else json.dumps(bounded_json(body, 8 * 1024 * 1024), allow_nan=False).encode()
        )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.key:
            headers["api-key"] = self.key
        request = Request(self.base + path, data=encoded, method=method, headers=headers)
        try:
            with open_pinned_url(
                request,
                timeout_seconds=self.timeout,
                purpose="Qdrant API",
                pinned_ips=self.ips,
            ) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise Failure("response_too_large", "Qdrant response exceeded 8 MiB.")
        except HTTPError as exc:
            codes = {
                400: "invalid_payload",
                401: "authentication_failed",
                403: "authentication_failed",
                404: "not_found",
                409: "conflict",
                429: "rate_limited",
            }
            raise Failure(
                codes.get(exc.code, "provider_unavailable"),
                f"Qdrant returned HTTP {exc.code}.",
            ) from exc
        except (OSError, URLError, ValueError) as exc:
            raise Failure(
                "provider_unavailable",
                "Qdrant request failed; check endpoint and connectivity.",
            ) from exc
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("status") != "ok" or "result" not in data:
                raise ValueError("Invalid envelope")
            return data["result"]
        except (ValueError, TypeError) as exc:
            raise Failure(
                "invalid_provider_response", "Qdrant returned an invalid JSON result."
            ) from exc
