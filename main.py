"""Flow Steward Qdrant host-v2 subprocess entrypoint."""

from __future__ import annotations
import json
import sys
from flowsteward_extension_sdk import runtime_context_from_payload
from citations import finalize_answer
from documents import bounded_string, prepare_document
from qdrant import CONNECTED, WRITES, Client
from validation import Failure, require


def handle(payload):
    client = None
    try:
        runtime_context_from_payload(payload)
        require(payload.get("mode") == "action", "An action invocation is required.")
        action = payload.get("action") or {}
        name = action.get("action_id")
        args = action.get("input") or {}
        require(isinstance(args, dict), "Action input must be an object.")
        context = payload["runtime_context"]
        require(type(context.get("test_mode", False)) is bool, "test_mode must be boolean.")
        if name == "prepare_document":
            result = prepare_document(**args)
        elif name == "prepare_query":
            question = bounded_string(args.get("question"), "question", 65536, required=True)
            result = {"question": question, "texts": [question]}
        elif name == "finalize_answer":
            result = finalize_answer(**args)
        elif name in CONNECTED:
            connection = (action.get("target") or {}).get("connection") or {}
            if (
                not isinstance(connection, dict)
                or not args.get("connection_ref")
                or connection.get("connection_id") != args.get("connection_ref")
                or connection.get("connection_type") != "qdrant"
            ):
                raise Failure(
                    "invalid_connection",
                    "Select the matching project Qdrant connection.",
                )
            client = Client(connection, context)
            if name in WRITES:
                if context.get("test_mode"):
                    raise Failure(
                        "test_mode_suppressed",
                        "Qdrant writes are suppressed in test mode.",
                    )
                claim = context.get("external_effect_id")
                if not isinstance(claim, str) or not claim.strip() or len(claim) > 512:
                    raise Failure(
                        "effect_claim_required",
                        "A durable external-effect claim is required for Qdrant writes.",
                    )
            result = client.execute(name, args)
        else:
            raise Failure("unknown_action", "Unknown Qdrant action.")
        require(
            len(json.dumps(result, allow_nan=False).encode("utf-8")) <= 480 * 1024,
            "Result exceeds the host inline budget; split the document or reduce retrieval size.",
        )
        return {"ok": True, "result": result, "errors": []}
    except (
        Failure,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        RecursionError,
    ) as exc:
        code = exc.code if isinstance(exc, Failure) else "invalid_payload"
        message = (
            str(exc)
            if isinstance(exc, Failure)
            else "Invalid host envelope or bounded action input."
        )
        uncertain = bool(client and client.attempted_write)
        return {
            "ok": False,
            "error_code": code,
            "error": message,
            "errors": [{"code": code, "message": message}],
            "retryable": False,
            "definitely_no_external_effect": not uncertain,
            "external_effect_status": "timeout_unknown" if uncertain else "not_applied",
        }
    except Exception:
        uncertain = bool(client and client.attempted_write)
        return {
            "ok": False,
            "error_code": "provider_unavailable",
            "error": "Qdrant operation failed.",
            "errors": [{"code": "provider_unavailable", "message": "Qdrant operation failed."}],
            "retryable": False,
            "definitely_no_external_effect": not uncertain,
            "external_effect_status": "timeout_unknown" if uncertain else "not_applied",
        }


def main():
    try:
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        require(len(raw) <= 16 * 1024 * 1024, "Input exceeds 16 MiB.")
        response = handle(json.loads(raw))
    except (ValueError, TypeError, Failure):
        response = {
            "ok": False,
            "error_code": "invalid_payload",
            "error": "Invalid bounded JSON input.",
            "errors": [],
        }
    print(json.dumps(response, allow_nan=False))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
