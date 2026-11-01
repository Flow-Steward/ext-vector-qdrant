"""Pure, bounded, lossless document preparation for vector ingestion."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
RESERVED_KEYS = frozenset(
    {
        "id",
        "score",
        "document_id",
        "chunk_id",
        "chunk_index",
        "text",
        "start",
        "end",
        "source_uri",
        "title",
        "metadata",
        "content_hash",
        "source_id",
        "excerpt",
        "fs_scope",
    }
)


def bounded_string(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    """Validate UTF-8 text without altering its contents."""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{name} must be a string with the required content")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc
    if size > maximum:
        raise ValueError(f"{name} exceeds its UTF-8 byte limit")
    return value


def safe_source_uri(value: Any) -> str:
    """Permit descriptive HTTP(S)/artifact references, never credentials."""
    uri = bounded_string(value, "source_uri", 4096)
    if not uri:
        return uri
    if any(ord(char) < 33 or ord(char) == 127 for char in uri) or "\\" in uri:
        raise ValueError("source_uri contains unsafe characters")
    try:
        parsed = urlsplit(uri)
        if parsed.scheme not in {"http", "https", "artifact"} or not parsed.netloc:
            raise ValueError("source_uri must be an HTTP(S) or artifact URI")
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            raise ValueError("source_uri must not contain credentials")
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("source_uri is invalid") from exc
    return uri


def safe_metadata(value: Any) -> dict:
    """Copy bounded JSON metadata and prevent internal payload-key spoofing."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    if any(
        not isinstance(key, str) or key in RESERVED_KEYS or key.startswith("_") for key in value
    ):
        raise ValueError("metadata contains a reserved key")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds its JSON byte limit")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise ValueError("metadata must be bounded JSON") from exc


def prepare_document(
    document_id: str,
    text: str,
    *,
    source_uri: str = "",
    title: str = "",
    metadata: dict | None = None,
    chunk_size: int = 2000,
    overlap: int = 200,
    max_chunks: int = 512,
) -> dict:
    """Return exact overlapping slices; fail rather than dropping excess text.

    IDs depend on document and ordinal, so a replacement reuses those slots and
    the connector can remove trailing slots when a document shrinks.
    """
    bounded_string(document_id, "document_id", 256, required=True)
    bounded_string(text, "text", MAX_DOCUMENT_BYTES, required=True)
    bounded_string(title, "title", 1024)
    safe_source_uri(source_uri)
    copied_metadata = safe_metadata(metadata)
    if type(chunk_size) is not int or not 128 <= chunk_size <= 16000:
        raise ValueError("chunk_size must be an integer from 128 to 16000")
    if type(overlap) is not int or not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be nonnegative and smaller than chunk_size")
    if type(max_chunks) is not int or not 1 <= max_chunks <= 512:
        raise ValueError("max_chunks must be an integer from 1 to 512")
    step = chunk_size - overlap
    count = 1 + max(0, (len(text) - chunk_size + step - 1) // step)
    if count > max_chunks:
        raise ValueError("document exceeds max_chunks; increase chunk_size or reduce overlap")
    chunks = []
    for index in range(count):
        start = index * step
        end = min(start + chunk_size, len(text))
        chunks.append(
            {
                "chunk_id": str(
                    uuid5(NAMESPACE_URL, json.dumps([document_id, index], ensure_ascii=False))
                ),
                "document_id": document_id,
                "chunk_index": index,
                "text": text[start:end],
                "start": start,
                "end": end,
                "source_uri": source_uri,
                "title": title,
                "metadata": json.loads(json.dumps(copied_metadata)),
            }
        )
    return {
        "document_id": document_id,
        "chunks": chunks,
        "texts": [chunk["text"] for chunk in chunks],
        "chunk_count": count,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
