"""
Part 2 proof-of-concept: a tiny slice of the SYSTEM_DESIGN.md ingestion pipeline + status
tracking, running against the REAL Part 1 data (not dummy data — we have real data, may as well
use it), stdlib only (sqlite3) so it costs ~0 extra disk/deps on top of Part 1's venv.

What this simulates, mapped to SYSTEM_DESIGN.md components:
  - component #2 (ingest worker): reuses src/ingest.py's real load_patents() + field policy
  - component #3 (metadata DB `status` column): a `patents` table with a status per record,
    moving through pending -> ingested -> embedding -> indexed -> live (or -> failed)
  - error handling section: a `dead_letters` table for failed records, with a reason

Honest caveat (this is fast-and-sloppy PoC code, per the brief's allowance): the real Part 1
sample data has 0 naturally-failing records (see README.md), so there's nothing organic to
demonstrate dead-lettering on. To actually exercise that code path, this script randomly fails
a small fraction of records at the "embedding" stage (fixed seed, clearly logged as simulated —
NOT a real embedding failure). Everything else (ingestion, field validation, status transitions,
timing) is real, using the real 640 patents.

Run: python poc/pipeline_poc.py   (from repo root; rebuilds poc/pipeline.db from scratch each run)
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
from ingest import load_patents  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.db")
SIMULATED_EMBEDDING_FAILURE_RATE = 0.03  # 3% — purely to exercise the dead-letter path, see docstring
RNG_SEED = 42


# Drop and recreate the patents/dead_letters/pipeline_runs tables (fresh DB on every run).
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS patents;
        DROP TABLE IF EXISTS dead_letters;
        DROP TABLE IF EXISTS pipeline_runs;

        CREATE TABLE patents (
            doc_number TEXT PRIMARY KEY,
            title TEXT,
            classification TEXT,
            source_file TEXT,
            status TEXT NOT NULL,       -- pending | ingested | embedding | indexed | live | failed
            updated_at REAL NOT NULL
        );
        -- Real version of this = a B-tree index on classification + a full-text index on title
        -- (see SYSTEM_DESIGN.md component #3). SQLite indexes stand in for that here.
        CREATE INDEX idx_patents_classification ON patents(classification);
        CREATE INDEX idx_patents_status ON patents(status);

        CREATE TABLE dead_letters (
            doc_number TEXT,
            stage TEXT,
            reason TEXT,
            created_at REAL
        );

        CREATE TABLE pipeline_runs (
            run_started_at REAL,
            run_finished_at REAL,
            total_records INTEGER,
            excluded_at_ingest INTEGER,
            failed_at_embedding INTEGER,
            live INTEGER
        );
        """
    )
    conn.commit()


# Insert a patent row if it doesn't exist yet, or update its status if it does — used to move
# a record through the pipeline's stages (ingested -> embedding -> indexed -> live / failed).
def upsert_status(conn, doc_number, title, classification, source_file, status):
    conn.execute(
        """INSERT INTO patents (doc_number, title, classification, source_file, status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(doc_number) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
        (doc_number, title, classification, source_file, status, time.time()),
    )


# Record a failed patent in the dead_letters table with which stage it failed at and why.
def dead_letter(conn, doc_number, stage, reason):
    conn.execute(
        "INSERT INTO dead_letters (doc_number, stage, reason, created_at) VALUES (?, ?, ?, ?)",
        (doc_number, stage, reason, time.time()),
    )


# Load the real patent data, then walk every record through the simulated pipeline stages,
# writing status transitions (and any simulated failures) to SQLite. See module docstring.
def run_pipeline():
    run_start = time.time()
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print(f"[ingest] loading patent_data_small via src/ingest.py ...")
    patents, ingest_stats = load_patents(os.path.join(REPO_ROOT, "patent_data_small"))
    print(f"[ingest] loaded {len(patents)} patents, "
          f"{ingest_stats['excluded']} excluded (missing required fields) of {ingest_stats['total_records']} total")

    rng = random.Random(RNG_SEED)
    failed_at_embedding = 0

    for p in patents:
        # stage: ingested
        upsert_status(conn, p.doc_number, p.title, p.classification, p.source_file, "ingested")

        # stage: embedding (simulated — see module docstring re: injected failure rate)
        upsert_status(conn, p.doc_number, p.title, p.classification, p.source_file, "embedding")
        if rng.random() < SIMULATED_EMBEDDING_FAILURE_RATE:
            upsert_status(conn, p.doc_number, p.title, p.classification, p.source_file, "failed")
            dead_letter(conn, p.doc_number, "embedding",
                        "SIMULATED failure (injected for PoC demo, not a real error) — "
                        "stand-in for e.g. a model-server timeout on a malformed/huge document")
            failed_at_embedding += 1
            continue

        # stage: indexed -> live
        upsert_status(conn, p.doc_number, p.title, p.classification, p.source_file, "indexed")
        upsert_status(conn, p.doc_number, p.title, p.classification, p.source_file, "live")

    conn.commit()

    live_count = conn.execute("SELECT COUNT(*) FROM patents WHERE status='live'").fetchone()[0]
    run_finish = time.time()
    conn.execute(
        """INSERT INTO pipeline_runs
           (run_started_at, run_finished_at, total_records, excluded_at_ingest, failed_at_embedding, live)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_start, run_finish, ingest_stats["total_records"], ingest_stats["excluded"], failed_at_embedding, live_count),
    )
    conn.commit()
    conn.close()

    print(f"[pipeline] done in {run_finish - run_start:.2f}s. "
          f"{live_count} live, {failed_at_embedding} failed/dead-lettered, "
          f"{ingest_stats['excluded']} excluded at ingest.")
    print(f"[pipeline] wrote {DB_PATH} — run `python poc/dashboard.py` to view status.")


if __name__ == "__main__":
    run_pipeline()
