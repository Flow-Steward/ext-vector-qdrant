# Qdrant RAG workflows

Import the bundled workflows, `qdrant_grounded_answerer` agent, and
`qdrant_answer_from_sources` skill into the same project. Install Cohere Retrieval
1.0.2 or later (before 2.0) for embedding and Docling 0.3 or later (before 1.0)
for file ingestion. These are real provider operations and may incur provider
charges. No credentials, connection IDs, model IDs, or test responders are bundled.

Select project connections on each connected step using the normal connection
selector before running. The empty `connection_binding_jsonb` values deliberately
leave those selections unbound on import. Pure preparation/finalization steps do
not require a connection. Configure the project's agent provider/model normally;
the answer agent inherits that configuration and has no tools.

Use the same Cohere embedding model and dimensions for ingestion and questions.
Configure a Qdrant collection with the matching vector size and compatible
similarity metric, and select that same collection connection in ingestion and
question workflows. Changing embedding models requires re-embedding the corpus.

- `qdrant_ingest_text`: provide `document_id` and `text`, optionally `source_uri`
  and `title`. Prepares chunks, embeds them as `search_document`, then replaces
  that document's chunks. Reuse the document ID for an update.
- `qdrant_ingest_file`: provide `document_id` and a workflow `artifact_handle`,
  optionally provenance fields. Extracts Markdown with Docling, then follows the
  same ingestion path. Truncated Docling Markdown fails before embedding or
  replacement; increase the extraction limit or split the file and retry.
- `qdrant_ask`: provide `question`, optionally `top_k` (default 5). Embeds as
  `search_query`, retrieves bounded excerpts, requests a source-grounded answer,
  and validates citation IDs and markers. Empty retrieval skips the model and
  deterministically abstains. The final `answer`, `sources`, `citation_count`,
  `grounded`, and `abstained` variables are the public result. `grounded` means
  citation structure was validated; it is not an independent factuality proof.
- `qdrant_delete_document`: provide `document_id` to delete its chunks from the
  selected connection. This changes external data.

The ingestion templates prepare at most 512 chunks, each 2000 characters with
200-character overlap, subject to the connector's serialized inline-result limit
and Core's 256 KiB Batch input limit. Oversized documents fail explicitly without
truncation. Core Batch makes one-text lists; a sequential ForEach invokes Cohere
once per chunk and Collect appends the first returned vector in order. One-text
requests respect Cohere's dimension-dependent durable-result bound. Replacement
runs only after all requests succeed. The project operator's ForEach collection
limit must cover the prepared chunks (default 1000); a lower limit can stop early,
in which case replacement rejects the chunk/vector count mismatch before I/O. Replacement is a bounded multi-request
operation, not an atomic Qdrant transaction; inspect uncertain outcomes before
retrying. Provider failures stop the workflow before downstream steps.

Retrieved excerpts are untrusted data. The skill rejects instructions inside
excerpts, permits only excerpt-supported facts, requires markers such as `[S1]`,
and returns `answer`, `cited_source_ids`, and `abstained`. When nonempty excerpts
are insufficient, the skill returns an empty `answer`, no cited IDs, and
`abstained: true`. The answer field is optional in its Core output contract so
that this valid abstention reaches finalization, which returns a visible
insufficient-evidence reply with no citations. Invalid citations fail finalization
rather than being silently accepted.

Extension 1.0.2 ships `qdrant_answer_from_sources` 1.0.1 with this repaired
contract. Artifact IDs and workflow/agent references stay stable. Installing or
updating the extension exposes the corrected bundled skill; independently copied
project artifacts must be reviewed and reimported by their owner. No project
customizations are overwritten by this package.
