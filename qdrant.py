"""Project-scoped Qdrant operations with explicit document replacement."""

from __future__ import annotations
import hashlib
import json
import math
import re
from uuid import NAMESPACE_URL, uuid5
from citations import build_context
from documents import bounded_string, safe_metadata, safe_source_uri
from transport import Transport
from validation import (
    Failure,
    bounded_json,
    identifier,
    integer,
    require,
    user_filter,
    vector,
)

WRITES = {
    "ensure_indexes",
    "create_collection",
    "upsert_points",
    "delete_points",
    "replace_document",
    "delete_document",
}
CONNECTED = WRITES | {"get_collection", "get_points", "search"}


class Client:
    def __init__(self, connection: dict, context: dict):
        config = connection.get("config") or {}
        require(isinstance(config, dict), "Connection config must be an object.")
        collection = config.get("collection", "")
        if not isinstance(collection, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", collection):
            raise Failure(
                "invalid_connection",
                "Select a collection with 1–128 letters, digits, underscores or hyphens.",
            )
        self.path = "/collections/" + collection
        self.collection = collection
        self.name = config.get("vector_name", "")
        if not isinstance(self.name, str) or len(self.name.encode()) > 128:
            raise Failure("invalid_connection", "Invalid vector name.")
        for key in ("account_id", "project_id"):
            if not isinstance(context.get(key), str) or not context[key].strip():
                raise Failure("invalid_connection", "Account and project scope are required.")
        self.scope = hashlib.sha256(
            json.dumps(
                [
                    context["account_id"],
                    context["project_id"],
                    connection["connection_id"],
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.transport = Transport(config, connection.get("secrets") or {})
        self.attempted_write = False

    def request(self, method, path, body=None):
        return self.transport.request(method, path, body)

    def scoped(self, extra=None, *, kind=None, document=None):
        clauses = [{"key": "_fs_scope", "match": {"value": self.scope}}]
        if kind:
            clauses.append({"key": "_fs_kind", "match": {"value": kind}})
        if document is not None:
            clauses.append({"key": "document_id", "match": {"value": document}})
        if extra:
            clauses.append(user_filter(extra))
        return {"must": clauses}

    def point_id(self, logical, kind="point"):
        return str(
            uuid5(
                NAMESPACE_URL,
                json.dumps([self.scope, kind, logical], ensure_ascii=False),
            )
        )

    def dimension(self):
        result = self.request("GET", self.path)
        try:
            vectors = result["config"]["params"]["vectors"]
            config = vectors[self.name] if self.name else vectors
            size = config["size"]
            require(
                type(size) is int and 1 <= size <= 65536,
                "Collection dimensions are invalid.",
            )
            return size
        except (KeyError, TypeError) as exc:
            raise Failure(
                "invalid_connection",
                "Configured vector name does not match collection.",
            ) from exc

    def write(self, path, body, method="POST"):
        bounded_json(body, 8 * 1024 * 1024)
        self.attempted_write = True
        result = self.request(method, path, body)
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise Failure("invalid_provider_response", "Qdrant did not confirm completion.")
        return result

    def upsert(self, points):
        # Validate every batch before the first mutation.
        batches = [{"points": points[i : i + 32]} for i in range(0, len(points), 32)]
        for batch in batches:
            bounded_json(batch, 8 * 1024 * 1024)
        for batch in batches:
            self.write(self.path + "/points?wait=true&ordering=strong", batch, "PUT")

    def execute(self, action, args):
        if action == "ensure_indexes":
            self.ensure_indexes()
            return {"collection": self.collection, "indexes_ready": True}
        if action == "get_collection":
            return {
                "collection": self.collection,
                "info": self.request("GET", self.path),
            }
        if action == "create_collection":
            size = integer(args.get("dimensions"), "dimensions", 1, 65536)
            distance = args.get("distance", "Cosine")
            require(
                distance in {"Cosine", "Dot", "Euclid", "Manhattan"},
                "Unknown distance metric.",
            )
            config = {"size": size, "distance": distance}
            body = {"vectors": {self.name: config} if self.name else config}
            self.attempted_write = True
            require(
                self.request("PUT", self.path, body) is True,
                "Qdrant did not confirm creation.",
            )
            self.ensure_indexes()
            return {"collection": self.collection, "created": True}
        if action == "search":
            v = args.get("vector")
            if v is None:
                matrix = args.get("query_vectors")
                require(
                    isinstance(matrix, list) and len(matrix) == 1,
                    "query_vectors must contain exactly one vector.",
                )
                v = matrix[0]
            body = {
                "query": vector(v),
                "filter": self.scoped(args.get("filter")),
                "limit": integer(args.get("top_k", 5), "top_k", 1, 100),
                "with_payload": True,
            }
            if self.name:
                body["using"] = self.name
            if "score_threshold" in args:
                threshold = args["score_threshold"]
                require(
                    type(threshold) in (float, int) and math.isfinite(threshold),
                    "score_threshold must be finite.",
                )
                body["score_threshold"] = threshold
            result = self.request("POST", self.path + "/points/query", body)
            require(
                isinstance(result, dict) and isinstance(result.get("points"), list),
                "Invalid points response.",
            )
            matches = self.visible(result["points"])
            context = build_context(
                [m for m in matches if isinstance(m["payload"].get("text"), str)],
                max_characters=integer(
                    args.get("max_context_characters", 24000),
                    "max_context_characters",
                    1,
                    1000000,
                ),
            )
            return {"matches": matches, **context}
        if action == "get_points":
            ids = args.get("ids")
            require(
                isinstance(ids, list) and 1 <= len(ids) <= 128,
                "Supply 1 to 128 point IDs.",
            )
            internal = [self.point_id(identifier(i)) for i in ids]
            result = self.request(
                "POST",
                self.path + "/points",
                {"ids": internal, "with_payload": True, "with_vector": True},
            )
            require(isinstance(result, list), "Invalid points response.")
            return {"points": self.visible(result)}
        if action == "delete_document":
            doc = bounded_string(args.get("document_id"), "document_id", 256, required=True)
            self.write(
                self.path + "/points/delete?wait=true&ordering=strong",
                {"filter": self.scoped(kind="document", document=doc)},
            )
            return {"document_id": doc, "deleted": True}
        if action == "delete_points":
            ids = args.get("ids")
            if ids is not None:
                require(
                    isinstance(ids, list) and 1 <= len(ids) <= 128,
                    "Supply 1 to 128 point IDs.",
                )
                f = self.scoped(
                    {"must": [{"has_id": [self.point_id(identifier(i)) for i in ids]}]},
                    kind="point",
                )
            else:
                require(bool(args.get("filter")), "Supply point IDs or a nonempty filter.")
                f = self.scoped(user_filter(args["filter"]), kind="point")
            self.write(self.path + "/points/delete?wait=true&ordering=strong", {"filter": f})
            return {"deleted": True}
        if action == "upsert_points":
            rows = args.get("points")
            require(
                isinstance(rows, list) and 1 <= len(rows) <= 128,
                "Supply 1 to 128 points.",
            )
            points = []
            seen = set()
            for row in rows:
                require(isinstance(row, dict), "Each point must be an object.")
                logical = identifier(row.get("id"))
                require(logical not in seen, "Duplicate point ID.")
                seen.add(logical)
                payload = bounded_json(row.get("payload", {}))
                require(
                    isinstance(payload, dict)
                    and all(not k.startswith("_") and k != "fs_scope" for k in payload),
                    "Payload contains reserved keys.",
                )
                payload.update(_fs_scope=self.scope, _fs_kind="point", _fs_external_id=logical)
                points.append(
                    {
                        "id": self.point_id(logical),
                        "vector": vector(row.get("vector")),
                        "payload": payload,
                    }
                )
            self.validate_vectors(points)
            self.upsert(points)
            return {
                "upserted_count": len(points),
                "ids": [p["payload"]["_fs_external_id"] for p in points],
            }
        if action == "replace_document":
            doc = bounded_string(args.get("document_id"), "document_id", 256, required=True)
            chunks = args.get("chunks")
            vectors = args.get("vectors")
            require(
                isinstance(chunks, list) and 1 <= len(chunks) <= 512,
                "Supply 1 to 512 document chunks.",
            )
            require(
                isinstance(vectors, list) and len(vectors) == len(chunks),
                "One vector per chunk is required.",
            )
            points = []
            seen = set()
            for chunk, v in zip(chunks, vectors, strict=True):
                require(
                    isinstance(chunk, dict) and chunk.get("document_id") == doc,
                    "Chunks must belong to this document.",
                )
                cid = bounded_string(chunk.get("chunk_id"), "chunk_id", 256, required=True)
                require(cid not in seen, "Duplicate chunk ID.")
                seen.add(cid)
                payload = {
                    "document_id": doc,
                    "chunk_id": cid,
                    "chunk_index": integer(chunk.get("chunk_index"), "chunk_index", 0, 511),
                    "text": bounded_string(chunk.get("text"), "text", 65536, required=True),
                    "source_uri": safe_source_uri(chunk.get("source_uri", "")),
                    "title": bounded_string(chunk.get("title", ""), "title", 1024),
                    "metadata": safe_metadata(chunk.get("metadata")),
                    "_fs_scope": self.scope,
                    "_fs_kind": "document",
                }
                for key in ("start", "end"):
                    if key in chunk:
                        payload[key] = integer(chunk[key], key, 0, 1024 * 1024)
                points.append(
                    {
                        "id": self.point_id([doc, cid], "document"),
                        "vector": vector(v),
                        "payload": payload,
                    }
                )
            self.validate_vectors(points)
            self.upsert(points)
            f = self.scoped(kind="document", document=doc)
            f["must_not"] = [{"has_id": [p["id"] for p in points]}]
            self.write(self.path + "/points/delete?wait=true&ordering=strong", {"filter": f})
            return {"document_id": doc, "chunk_count": len(points), "replaced": True}
        raise Failure("unknown_action", "Unknown Qdrant action.")

    def ensure_indexes(self):
        for field in ("_fs_scope", "_fs_kind", "document_id"):
            self.write(
                self.path + "/index?wait=true",
                {"field_name": field, "field_schema": "keyword"},
                "PUT",
            )

    def validate_vectors(self, points):
        size = self.dimension()
        require(
            all(len(p["vector"]) == size for p in points),
            "Vector dimensions do not match collection.",
        )
        for p in points:
            if self.name:
                p["vector"] = {self.name: p["vector"]}

    def visible(self, points):
        result = []
        require(len(points) <= 128, "Qdrant returned too many points.")
        for point in points:
            require(
                isinstance(point, dict) and isinstance(point.get("payload"), dict),
                "Invalid point payload.",
            )
            payload = point["payload"]
            if payload.get("_fs_scope") != self.scope:
                continue
            public = {k: v for k, v in payload.items() if not k.startswith("_fs_")}
            result.append(
                {
                    **point,
                    "id": payload.get("_fs_external_id", point["id"]),
                    "payload": public,
                }
            )
        return result
