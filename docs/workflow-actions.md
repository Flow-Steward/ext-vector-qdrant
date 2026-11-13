# Workflow actions

Save a project Qdrant connection with API origin, collection, optional vector name and API key. Select it on connected actions. Run Create a vector collection or Ensure retrieval payload indexes for an existing collection. Generate embeddings separately; keep the same model, dimensions and vector name throughout ingestion and retrieval.

Ingest: Split document text into chunks → embed texts using another extension → Replace a document and its embeddings with chunks and ordered vectors. Ask: Prepare a question for embedding → embed texts → Search similar text and build context → pass context to an answer agent → Validate answer citations with the returned sources.

Select the named action in an Extension tools step. Map workflow values to its inputs and map the returned fields needed by later steps. Examples below contain sample data; replace artifact handles and embedding values with actual earlier-step outputs.

## Split document text into chunks

Split text locally into overlapping Unicode chunks with stable IDs, source information and a content hash. Returns chunks and ordered texts for an embedding step; does not call Qdrant or generate vectors.

- **Stable document ID**: Nonempty identifier, at most 256 UTF-8 bytes, shared by this document’s chunks. Reuse it to replace or delete the same document.
- **Document text**: Complete nonempty Unicode text to split. Maximum 1 MiB UTF-8; the bounded workflow result can require smaller documents. This action does not extract text from files.
- **Document source URI**: Optional HTTP(S) or artifact URI for provenance and returned citations. No embedded credentials; the connector does not fetch this URI.
- **Document title**: Optional human-readable title carried into chunks and retrieval sources; maximum 1024 UTF-8 bytes.
- **Document metadata (JSON object)**: Optional metadata carried into prepared chunks. Reserved internal fields cannot be supplied. Use a JSON object with your own document attributes.
- **Maximum characters per chunk**: Optional integer from 128 to 16000; default 2000 Unicode characters. Choose a size that fits your embedding model input limit.
- **Overlap characters**: Optional integer from 0 to chunk size minus one; default 200. Repeats text between adjacent chunks to preserve context.
- **Maximum chunks**: Optional integer from 1 to 512; default 512. Exceeding this count fails instead of truncating text; split the source or adjust chunk size.

Example input values:

```json
{
  "document_id": "manual_42",
  "text": "Refunds are available within 30 days.",
  "source_uri": "https://example.com/refund-policy",
  "title": "Refund policy",
  "metadata": {
    "language": "en",
    "category": "refunds"
  },
  "chunk_size": 2000,
  "overlap": 200,
  "max_chunks": 512
}
```

## Prepare a question for embedding

Validate a question and return its text in the shape needed by a later embedding step. Runs locally; does not search Qdrant or call an LLM.

- **Question to retrieve evidence for**: Nonempty question text to prepare for a later embedding step. Use the same embedding model and dimensions as the document chunks.

Example input values:

```json
{
  "question": "What is the refund deadline?"
}
```

## Validate answer citations

Check an answer’s citation markers and cited IDs against the sources returned by search. Returns cited sources or an abstention; citation consistency does not prove factual support and no LLM is called.

- **Answer with source markers**: Answer from a later agent step, using markers such as [S1]. Markers must exactly match Cited source IDs and refer to supplied search sources.
- **Cited source IDs (JSON array)**: IDs referenced by answer markers, for example ["S1"]. Must exactly match the markers and be present in Sources. For an explicit abstention use an empty array.
- **Search sources (JSON array)**: Map the complete sources output from the same Search similar text and build context step. Do not replace it with invented citations. Empty sources produce an abstention.
- **Answer is an abstention**: Optional boolean; default false. Enable when the answer declines to answer; then supply no source IDs or citation markers.

Example input values:

```json
{
  "answer": "Refunds are available within 30 days. [S1]",
  "cited_source_ids": [
    "S1"
  ]
}
```

## Create a vector collection

Create the connection collection with chosen dimensions and distance, then create keyword indexes for scoped retrieval. Requires Qdrant collection-administration access; generate embeddings separately.

- **Embedding dimensions**: Required integer from 1 to 65536. Match the exact length produced by the embedding model; a named vector follows the connection vector name.
- **Vector distance metric**: Optional: Cosine (default), Dot, Euclid or Manhattan. Choose the metric intended for your embeddings and keep it consistent for ingestion and retrieval.

Example input values:

```json
{
  "dimensions": 1536
}
```

## Inspect a vector collection

Read the connection collection configuration and vector schema. Use to verify dimensions, distance and any named vector before ingestion.


Uses the selected project connection; no additional action inputs.

## Save or replace vector points by ID

Save 1–128 raw points with numeric vectors and optional payload. Logical IDs are isolated by account, project and connection; generate matching embeddings before this step.

- **Vector points (JSON array)**: Supply 1–128 objects with unique logical id, vector and optional payload. Payload cannot use reserved internal keys. Vectors must match collection dimensions; the example needs a 3-dimensional collection.

Example input values:

```json
{
  "points": [
    {
      "id": "manual_42_part_1",
      "vector": [
        0.1,
        0.2,
        0.3
      ],
      "payload": {
        "text": "Refunds within 30 days.",
        "language": "en"
      }
    }
  ]
}
```

## Read vector points by ID

Read 1–128 logical point IDs within this account, project and connection. Returns stored point data without a similarity search.

- **Logical point IDs (JSON array)**: Supply 1–128 logical IDs used when saving raw points. The connector maps these IDs into this connection’s isolated point space.

Example input values:

```json
{
  "ids": [
    "manual_42_part_1"
  ]
}
```

## Delete vector points by IDs or filter

Delete raw points within this connection scope using explicit IDs or a nonempty Qdrant filter. Supply one selection method; does not delete the collection.

- **Logical point IDs (JSON array)**: Supply 1–128 logical IDs used when saving raw points. The connector maps these IDs into this connection’s isolated point space.
- **Qdrant payload filter (JSON)**: Optional Qdrant filter combined with mandatory connection scope. For deletion, provide either IDs or a nonempty filter. Omit for unrestricted search within this scope.

Example input values:

```json
{
  "ids": [
    "manual_42_part_1"
  ]
}
```

## Replace a document and its embeddings

Save prepared chunks with one matching embedding per chunk, then remove stale chunks for the same document. Requires ordered vectors from an embedding step; multiple Qdrant requests are not atomic.

- **Stable document ID**: Nonempty identifier, at most 256 UTF-8 bytes, shared by this document’s chunks. Reuse it to replace or delete the same document.
- **Prepared document chunks (JSON array)**: Map chunks from Split document text into chunks. Supply 1–512 chunks with this document_id, unique chunk_id, chunk_index and text. Each chunk must have a matching vector in the same position.
- **Chunk embeddings (JSON matrix)**: Map ordered vectors from your embedding step, one numeric array per prepared chunk. Count and order must match Chunks and dimensions must match the collection. Example is for a 3-dimensional collection.

Example input values:

```json
{
  "document_id": "manual_42",
  "chunks": [
    {
      "document_id": "manual_42",
      "chunk_id": "manual_42_part_1",
      "chunk_index": 0,
      "text": "Refunds within 30 days."
    }
  ],
  "vectors": [
    [
      0.1,
      0.2,
      0.3
    ]
  ]
}
```

## Delete all chunks of a document

Delete all stored chunks for a stable document ID within this account, project and connection. Does not delete the collection or raw points.

- **Stable document ID**: Nonempty identifier, at most 256 UTF-8 bytes, shared by this document’s chunks. Reuse it to replace or delete the same document.

Example input values:

```json
{
  "document_id": "manual_42"
}
```

## Search similar text and build context

Search the connection collection using a query embedding, optional Qdrant filter and score threshold. Returns matches, exact source excerpts and bounded context for an answer step; does not generate embeddings or an answer.

- **Question embedding (JSON array)**: Supply a numeric embedding from an earlier step, using the document embedding model and collection dimensions. Use this field or Query embeddings, not question text. Example is 3-dimensional.
- **Query embeddings (one-row JSON matrix)**: Alternative to Question embedding: map the vectors output from embedding exactly one question. Must contain exactly one numeric vector matching the collection dimensions.
- **Qdrant payload filter (JSON)**: Optional Qdrant filter combined with mandatory connection scope. Document metadata is nested under metadata, for example metadata.language; raw-point payload fields use their own paths. Omit to search this scope without an additional filter.
- **Maximum matches**: Optional integer from 1 to 100; default 5. Increase only when the answer needs more source context.
- **Similarity score threshold**: Optional finite number forwarded to Qdrant. Interpretation depends on the collection metric; it is not a confidence percentage. Leave blank when first evaluating retrieval quality.
- **Maximum context characters**: Optional integer from 1 to 1000000; default 24000. Bounds assembled excerpts for a later answer agent; the host result-size limit still applies.

Example input values:

```json
{
  "vector": [
    0.1,
    0.2,
    0.3
  ],
  "filter": {
    "must": [
      {
        "key": "metadata.language",
        "match": {
          "value": "en"
        }
      }
    ]
  },
  "top_k": 5,
  "max_context_characters": 24000
}
```

## Ensure retrieval payload indexes

Create keyword indexes for _fs_scope, _fs_kind and document_id on an existing collection. Required for scoped retrieval under Qdrant strict mode; needs administration privileges.


Uses the selected project connection; no additional action inputs.
