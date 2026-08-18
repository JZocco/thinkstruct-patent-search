# Part 2 PoC: pipeline status tracking

A ~30-minute proof-of-concept of one slice of [`SYSTEM_DESIGN.md`](../SYSTEM_DESIGN.md): the
ingest pipeline's **status tracking** (component #3's `status` column) and **error handling**
(dead-letter table), backed by SQLite. Deliberately fast-and-sloppy per the brief's allowance for
this part — not meant to be a production pattern, just a tangible version of "here's what tracking
pipeline status actually looks like" that an intern could point at.

**Why SQLite instead of the Docker/Postgres PoC originally planned:** the dev machine's disk ran
critically low while building this (see `CLAUDE.md`'s "Disk space incident" section) — Docker
image pulls + Postgres weren't a safe bet at 1-2GB free. SQLite is stdlib (`sqlite3`), needs zero
extra installs, and the resulting `.db` file is a few hundred KB. Same *idea* (indexed metadata
table + status tracking) as the Postgres component in `SYSTEM_DESIGN.md`, much lower footprint.

## What it does

- `pipeline_poc.py` runs the **real** 640 patents from `patent_data_small/` through
  `src/ingest.py`'s actual loader, then walks each one through simulated pipeline stages
  (`ingested → embedding → indexed → live`), writing status transitions to `poc/pipeline.db`.
  A small fraction (3%, fixed seed) of records are randomly failed at the "embedding" stage —
  **clearly logged as simulated**, since the real sample data has 0 naturally-failing records
  (see main README) and there was nothing organic to demonstrate dead-lettering on otherwise.
- `dashboard.py` reads that DB and prints a status breakdown: counts per pipeline stage, a
  per-classification breakdown of what's live, and the most recent dead-lettered records with
  their failure reason.

## How to run

```bash
# from repo root, with the Part 1 venv active
source .venv/bin/activate

python poc/pipeline_poc.py      # rebuilds poc/pipeline.db from scratch, ~instant on 640 patents
python poc/dashboard.py         # one-shot status snapshot
python poc/dashboard.py --watch # refreshes every 3s (Ctrl+C to stop) — more like a real dashboard
```

## Known limitations (stated plainly, per the brief's "acknowledge its challenges")

- The "embedding" stage doesn't actually call the embedding model — it's a status transition with
  a simulated failure rate, not a real timing/failure profile of `sentence-transformers.encode()`.
- SQLite is a stand-in for Postgres — fine for this PoC's scale (640 rows), not a claim that
  SQLite is the right choice at 10M patents (see `SYSTEM_DESIGN.md` for the real recommendation).
- No queue/worker separation — this is a single script doing everything serially, not the
  actual job-queue architecture described in `SYSTEM_DESIGN.md` component #2.
