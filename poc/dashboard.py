"""
Part 2 PoC: a minimal status dashboard reading poc/pipeline.db (written by pipeline_poc.py).
Stdlib only. Prints a snapshot; pass --watch to refresh every few seconds like a real dashboard
would (polling the same DB — a real version would poll the metadata DB from SYSTEM_DESIGN.md).

Run: python poc/dashboard.py [--watch]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.db")


# Query the pipeline DB and build the full dashboard text: last run summary, status breakdown,
# per-classification counts, and recent dead letters. Returns the rendered string (not printed
# directly, so --watch can clear the screen and reprint it on each refresh).
def render(conn: sqlite3.Connection) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("PATENT INGESTION PIPELINE — STATUS DASHBOARD (PoC)")
    lines.append("=" * 72)

    run = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY run_started_at DESC LIMIT 1"
    ).fetchone()
    if run:
        started, finished, total, excluded, failed, live = run
        lines.append(f"Last run: {finished - started:.2f}s  |  "
                      f"{total} records seen  |  {excluded} excluded at ingest  |  "
                      f"{failed} failed/dead-lettered  |  {live} live")
    lines.append("")

    lines.append("-- Status breakdown --")
    for status, count in conn.execute(
        "SELECT status, COUNT(*) FROM patents GROUP BY status ORDER BY count(*) DESC"
    ):
        bar = "#" * min(count // 5, 50)
        lines.append(f"  {status:10s} {count:5d}  {bar}")
    lines.append("")

    lines.append("-- Live patents by classification (4-char prefix) --")
    for prefix, count in conn.execute(
        """SELECT substr(classification, 1, 4), COUNT(*) FROM patents
           WHERE status='live' GROUP BY substr(classification, 1, 4) ORDER BY count(*) DESC"""
    ):
        lines.append(f"  {prefix:6s} {count:5d}")
    lines.append("")

    dead = conn.execute(
        "SELECT doc_number, stage, reason FROM dead_letters ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    lines.append(f"-- Dead letters (most recent {len(dead)} of "
                  f"{conn.execute('SELECT COUNT(*) FROM dead_letters').fetchone()[0]}) --")
    if not dead:
        lines.append("  (none)")
    for doc_number, stage, reason in dead:
        lines.append(f"  {doc_number}  [{stage}]  {reason}")

    lines.append("=" * 72)
    return "\n".join(lines)


# CLI entrypoint: parse args, open the DB, and either print one snapshot or loop with --watch.
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="Refresh every 3s until Ctrl+C")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"No {DB_PATH} found — run `python poc/pipeline_poc.py` first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    if not args.watch:
        print(render(conn))
        return

    try:
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print(render(conn))
            print("\n(refreshing every 3s — Ctrl+C to stop)")
            time.sleep(3)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
