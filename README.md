# Qdrant Vector Store

Flow Steward extension `flowsteward.qdrant`, version **1.0.4**.

Build document retrieval and cited-answer workflows around a Qdrant collection. Prepare text into overlapping chunks with stable IDs and provenance, replace a document’s stored chunks, delete a document, or work directly with vector points through upsert, get and delete operations. Dense vector search accepts filters and score thresholds and returns matches, source identifiers, exact excerpts and assembled context for downstream steps.

Collection actions create or inspect the selected collection and ensure the payload indexes required for scoped retrieval. Data access is tied to the account, project and connection. Query preparation and answer finalization support source-based responses: finalization checks citation IDs and markers, and empty retrieval produces an abstention.

Bundled workflows cover text ingestion, file ingestion, questions and document deletion. They compose Cohere Retrieval for embeddings, Docling for file conversion and the project’s configured answer agent; those services need their own installation and connections. The Qdrant connector itself does not generate embeddings or run an LLM. Use matching embedding models and dimensions for ingestion and questions. Citation checks validate references against retrieved sources, helping later workflow steps present excerpts and citations alongside the answer.

Configure a project connection with Qdrant API origin, collection, optional vector name and API key, then create the collection or ensure payload indexes on an existing one. For bundled workflows, install Cohere Retrieval and Docling, import the workflow/agent/skill artifacts, select their connections and configure the project answer model.

## Setup

Create a project Qdrant connection with the API origin, collection name, optional vector name and API key. Use a dedicated collection for this connection. Origins must have no credentials, path, query or fragment. Public endpoints require HTTPS. Private/self-hosted endpoints require the administrator's `FS_ALLOW_PRIVATE_REMOTE_URLS` permission; local HTTP is allowed only for private/local addresses. The host egress policy still applies. Secrets are taken only from the selected connection.

Run **Create collection** with the embedding dimension and distance (`Cosine` by default). This also creates keyword indexes for `_fs_scope`, `_fs_kind`, and `document_id`, needed by Qdrant Cloud strict mode. For an existing collection run **Ensure payload indexes** before ingestion or retrieval. Named vectors must already match the connection's vector name. Use the same embedding model, dimensions and vector name for ingestion and questions; model changes require reindexing.

The connector does not generate embeddings or run an LLM. Bundled runnable workflows compose Cohere Retrieval for embeddings, Docling for file conversion and the project's configured agent for answers. See [workflow setup](artifacts/README.md). Provider calls may be billed. Install the optional dependencies and select project connections on imported steps.

## Operations

- `prepare_document`: lossless Unicode chunks, stable document/ordinal IDs, provenance, metadata and a content hash. Default 2000 characters, overlap 200; configurable up to 512 chunks and 1 MiB UTF-8 source. Excess input fails explicitly. Host invocation results are capped at 480 KiB (including repeated texts and escaped Unicode); larger sources must be split into documents.
- `replace_document`: prevalidate all chunks and vector dimensions; upsert in bounded batches, then remove stale chunks from that document. Reusing `document_id` updates it, including shrinkage. The host serializes replace/delete for the same connection/document.
- `delete_document`: remove all its chunks in this project's selected connection.
- `upsert_points`, `get_points`, `delete_points`: bounded raw point CRUD. Logical IDs are mapped to scope-specific Qdrant UUIDs. Raw points and document chunks have separate ID spaces. Delete by IDs or an explicit nonempty filter.
- `search`: dense vector or a one-element `query_vectors` matrix, optional Qdrant filter/threshold, top-k up to 100; returns matches, bounded context, exact excerpts and source identifiers. Qdrant enforces query dimensions. Mandatory project/connection scope cannot be overridden by supplied filters.
- `prepare_query`, `finalize_answer`: prepare query embedding input and require answer citation markers to match known source IDs. Empty retrieval deterministically abstains. `grounded` indicates citation consistency, not an independent proof of factual entailment. Retrieved content is untrusted evidence.
- `create_collection`, `get_collection`, `ensure_indexes`: configure/inspect the selected collection. Collection administration requires an appropriately privileged Qdrant key. Deletion of an entire collection is deliberately unavailable.

Writes require the host's durable external-effect claim and are suppressed in test mode. No automatic HTTP retries. Document replacement spans several Qdrant requests and is **not atomic**. Readers can observe an intermediate replacement. A failure after mutation starts returns `external_effect_status: timeout_unknown` and `retryable: false`; inspect the external state before an intentional reconciliation/retry. Stable IDs make a complete replacement convergent, but do not turn a partial operation into a transaction. Scope includes account, project and connection ID: moving to a new connection requires an explicit reindex, and direct pre-existing points without connector scope are not visible.

## Verification

Run the shared Docker gates from the Flow Steward checkout:

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root /absolute/path/to/this/repository
```

Tests cover chunking, citations, host envelopes, wire contracts and current Core workflow validators/selectors. Real Qdrant tests are opt-in: create ignored `tests/live_qdrant.local.json` containing `{"url":"http://isolated-qdrant:6333"}` accessible from `fs-audit`, then run the same gate. They create unique disposable collections; use a disposable server. They exercise default/named vectors, document replacement/shrinkage, scope isolation, point CRUD and cited-answer/abstention plumbing. The answer and embeddings in this test are deterministic fixtures; no paid embedding/LLM end-to-end execution is claimed.

API references: [Qdrant REST API](https://api.qdrant.tech/), [payload indexes](https://qdrant.tech/documentation/concepts/indexing/), [strict mode](https://qdrant.tech/documentation/guides/administration/).

## Automated releases

CI runs on pull requests and pushes to `main`. Official releases run through the
pinned shared GitHub Actions workflow using `.github/release-config.json` to test,
package both Linux runtime targets, and publish the release assets automatically.
Manual ZIP uploads are not part of the release process.

After merging the version **1.0.4** update into `main`, create and push its tag:

```sh
git switch main
git pull --ff-only
git tag -a v1.0.4 -m "Release v1.0.4"
git push origin v1.0.4
```

To rerun the workflow for an existing tag without moving it:

```sh
gh workflow run release.yml --ref main -f tag=v1.0.4
```

The manifest remains the source for release identity and catalog descriptions.
The shared workflow installs the verified first-party SDK wheel before test
dependencies. Where native payloads are absent from source, the recipe restores
only the listed missing files from checksum-pinned published archives; the source
packager still verifies their provenance before packaging.
