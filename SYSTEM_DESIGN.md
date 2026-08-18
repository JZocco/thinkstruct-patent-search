# System Design: Patent Search at 10M-Patent Scale

Audience: an intern who's read the Part 1 code (`src/ingest.py`, `index.py`, `search.py`) and
needs to know what changes to actually run this on the full patent database. This is
deliberately **not** a perfect design — it's a simple one with its weaknesses called out, per the
brief's own guidance.

## The gap between Part 1 and "real"

Part 1 works because everything fits in one process: 640 patents, a few hundred KB of JSON, an
embedding matrix that fits in RAM, all rebuilt from scratch on every cold start. None of that
holds at 10M patents:
- 10M patents × ~5-10KB of text each ≈ 50-100GB of raw text — doesn't comfortably fit in memory.
- Re-embedding everything from scratch on every run is a multi-hour-to-multi-day batch job, not
  something you do per query.
- New patents arrive every week (this is literally how the sample data is organized —
  `patents_ipa{DATE}.json` per filing week) — the system needs to keep up incrementally, not just
  do one big load.
- One process can't serve concurrent search traffic while also running ingestion.

So the design below splits Part 1's single script into: **storage** (durable, shared), **pipelines**
(ingest → normalize → embed → index, running continuously/incrementally), and a **serving layer**
(stateless, horizontally scalable, read-only against the storage).

## Components

```
                    ┌─────────────────┐
  USPTO bulk data →  │ 1. Raw storage   │  (S3/GCS bucket — original JSON/XML, source of truth)
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ 2. Ingest worker  │  parses + validates + normalizes (= ingest.py today)
                    │   (job queue)     │  writes to metadata DB, enqueues embedding job
                    └────────┬─────────┘
                             │
              ┌──────────────┼───────────────┐
              ▼                              ▼
   ┌──────────────────┐          ┌───────────────────────┐
   │ 3. Metadata DB     │          │ 4. Embedding worker    │
   │   (Postgres)       │          │   (batched, GPU-ish)   │
   │  doc_number, title, │          │  = index.py's model    │
   │  classification,    │          │    .encode() step      │
   │  abstract, status    │          └───────────┬───────────┘
   └──────────┬─────────┘                        │
              │                                   ▼
              │                        ┌───────────────────────┐
              │                        │ 5. Vector index         │
              │                        │   (sharded ANN store)   │
              │                        └───────────┬───────────┘
              │                                    │
              └──────────────┬─────────────────────┘
                             ▼
                  ┌───────────────────┐
                  │ 6. Search API       │  = search.py's PatentIndex.search(), but stateless
                  │  (stateless, many   │    and reading from #3 + #5 instead of local files
                  │   replicas)         │
                  └──────────┬─────────┘
                             │
                  ┌──────────▼─────────┐
                  │ 7. Status/monitoring │  (this Part's POC is a tiny slice of this)
                  └─────────────────────┘
```

1. **Raw storage (S3/GCS).** Every filing JSON/XML gets archived here first, untouched. This is
   the thing you can always reprocess from if a downstream step has a bug — never derive anything
   without keeping the source.
2. **Ingest worker(s), behind a job queue (SQS/Kafka).** One job per filing-week file (or per
   patent, at this scale probably per-file to reduce queue overhead). Does what `ingest.py` does
   today: parse, apply the required-field policy, clean `detailed_description`. On success, writes
   a row to the metadata DB and enqueues an embedding job. On failure, goes to a dead-letter queue
   (see Error Handling) instead of silently dropping or crashing the batch.
3. **Metadata DB (Postgres).** One row per patent: `doc_number, title, abstract, classification,
   filing_week, status, ...`. Indexed with a B-tree on `classification` and a `tsvector`
   (full-text) index on `title`/`abstract` — this is the real version of `metadata_mask()` from
   `index.py`, which today is just a Python list scan. `status` tracks the row through the
   pipeline (`ingested → embedding → indexed → live`, or `failed`).
4. **Embedding worker(s).** Pulls newly-ingested patents, runs the same `sentence-transformers`
   model from Part 1 in batches (batching is what makes this remotely affordable — see Cost
   below), writes vectors to the vector index. This is the one component I'd consider swapping to
   a GPU-backed batch service at this scale — CPU encoding is fine for 640 patents, not for 10M.
5. **Vector index (sharded ANN store).** Not a flat numpy matrix anymore — something like FAISS
   (self-hosted, sharded) or a managed vector DB (pgvector at moderate scale, or
   Milvus/Weaviate/Pinecone beyond that). **Sharded by classification prefix.** This directly
   follows from the Part 1 finding: hybrid queries filter heavily by classification, and at 10M
   patents that filter is far more selective than any of the ~0.2%-46% examples measured in Part
   1. Sharding by classification turns "filter by classification" into "query only the relevant
   shard(s)" — no runtime filtering cost at all, and it parallelizes both ingestion and query load.
6. **Search API.** Stateless, horizontally scaled. A hybrid query first hits the metadata DB's
   index to resolve which shard(s)/candidates match the metadata constraint, then does ANN search
   against just those vector shards, then merges/ranks. This is `PatentIndex.search()`'s logic,
   just backed by real indexes instead of an in-memory numpy array and a pickle file.
7. **Monitoring.** Pipeline health (queue depth, failure rate, embedding backlog age), index
   freshness (time since last patent went live), and query-serving metrics (latency, error rate).
   The Part 2 POC (see below) is a deliberately tiny slice of this — a local dashboard showing
   ingestion pipeline status, so an intern can see what a "just enough to monitor" version looks
   like before building the real thing.

## Error handling

- **Ingestion failures** (missing required field, malformed JSON, encoding issues): route to a
  dead-letter queue instead of dropping silently or crashing the whole batch job — the Part 1
  `ingest.py` policy (exclude records missing `title`/`doc_number`/`abstract`/`classification`) is
  fine for a demo but at scale you want those failures visible and triageable, not just counted.
- **Embedding failures** (OOM on a huge document, model server timeout): retry with backoff, then
  dead-letter after N attempts. Never let one bad document stall a whole batch.
- **Partial pipeline state**: a patent that's in the metadata DB but not yet embedded should not
  appear in search results (hence the `status` column) — better to be briefly missing from search
  than to serve a stale/incomplete record.

## Tracking contents & status

- The `status` column per patent (mentioned above) is the cheapest version of "what's in the
  system and is it done" — a query like `SELECT status, count(*) FROM patents GROUP BY status`
  answers "how much is backlogged" without any extra infrastructure.
- A dead-letter table/queue with the original payload + failure reason answers "what got excluded
  and why" — the Part 1 README's honesty about "0 excluded on this sample, untested on real gaps"
  is exactly the kind of thing this table would surface immediately once real data hits it.

## Rough cost breakdown (order-of-magnitude, not a quote)

Assumptions: 10M patents, ~10-30KB of text each, weekly incremental filings on top (~50-100k/week
based on real USPTO volume), 384-dim embeddings (same model as Part 1).

| Component | Rough size | Rough monthly cost |
|---|---|---|
| Raw storage (S3, all filings archived) | ~10M × ~20KB ≈ 200GB | ~$5/mo |
| Metadata DB (Postgres, managed) | 10M rows, few GB + indexes | ~$150-300/mo (mid-size managed instance) |
| Vector storage (10M × 384 × 4 bytes, + ANN index overhead ~2-3x) | ~15GB raw, ~30-45GB w/ index | ~$200-800/mo (self-hosted on block storage vs. managed vector DB) |
| Embedding compute — **initial backfill** (one-time) | 10M docs, batched | ~$500-3,000 one-time (highly dependent on batch size / CPU vs GPU; this is the number I'd actually go measure before committing to it) |
| Embedding compute — steady state (weekly new filings) | ~50-100k docs/week | ~$20-100/mo |
| Search API compute (few replicas, autoscaled) | — | ~$150-400/mo |
| Job queue / orchestration | — | ~$30-100/mo |
| **Total steady-state** | | **roughly $500-1,700/month**, + a one-time backfill cost in the low thousands |

This is a back-of-envelope estimate meant to be *directionally* useful (e.g. "vector storage and
metadata DB dominate, not the queue"), not a number I'd take to a budget meeting without actually
benchmarking the embedding batch throughput first.

## Major challenges at this scale (acknowledged, not solved here)

- **Initial backfill is the scary part.** Embedding 10M documents from a cold start is a multi-day
  job even with good batching/parallelism, and if the embedding model changes later, you're
  potentially redoing all of it. Worth versioning embeddings by model so a re-embed can be a
  rolling migration, not a stop-the-world event.
- **Classification isn't evenly distributed.** Our sample is 100% `B60*` (vehicles) by
  construction; the real corpus spans thousands of classification codes with wildly different
  patent counts. Sharding by classification prefix (as proposed above) risks hot/oversized shards
  for common categories — would need monitoring on shard size and a rebalancing plan, not just a
  fixed static shard assignment.
- **Dual-write consistency** between the metadata DB and the vector index (two different systems
  that both need to reflect "this patent is live") is a real distributed-systems problem — the
  `status` column approach here is the simple/eventually-consistent version, not a transactional
  one. Good enough for search (a patent showing up a few minutes late is fine); would not be good
  enough for something transactional.
- **Freshness vs. index quality trade-off.** True ANN indexes (HNSW, IVF) get more expensive to
  rebuild as they grow; incremental inserts are supported by most modern ANN libraries but degrade
  index quality over time compared to a full rebuild. Needs a periodic full-reindex job, which is
  itself a scaled-down version of the backfill problem above.
- **Data quality drifts more at scale.** Older/OCR'd patents, foreign-language filings (explicitly
  out of scope for this submission, but a real production system built on this would hit it fast
  given the brief's own mention of expansion into other geographies), inconsistent classification
  code formats — the clean "0 excluded" result from the Part 1 sample will not hold on the real
  corpus, and the dead-letter/monitoring pieces above exist specifically because of that.
