"""
Load and normalize the patent JSON files into a flat list of records ready for indexing.

Missing-field policy (see CLAUDE.md / README for more):
  - `title`, `doc_number`, `abstract`, `classification` are considered required — a record
    missing (null/empty) any of these is EXCLUDED, since they're needed for basic display,
    identity, and the classification hybrid filter.
  - `claims` and `detailed_description` are treated as optional/best-effort: a record is kept
    even if these are empty, but a record with an empty `claims` list also gets `abstract` used
    as a fallback for whatever text field the search needs (claims are still preferred when
    present, since they're the legally operative text).
  - Empty-string paragraphs inside `detailed_description` (an XML-parsing artifact present in
    ~50% of entries in the sample data) are stripped out.
On the small sample dataset provided, no records were actually excluded (see CLAUDE.md) — this
policy is exercised in code but effectively untested against real gaps in this dataset.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field


REQUIRED_FIELDS = ("title", "doc_number", "abstract", "classification")


@dataclass
class Patent:
    doc_number: str
    title: str
    abstract: str
    classification: str
    claims: list[str] = field(default_factory=list)
    detailed_description: list[str] = field(default_factory=list)
    filename: str = ""
    source_file: str = ""

    @property
    def claims_text(self) -> str:
        # All claims joined into a single string (used as the "document" side of the Part 3
        # eval task, and folded into `searchable_text` for embedding).
        return " ".join(self.claims)

    @property
    def description_text(self) -> str:
        # All detailed-description paragraphs joined into a single string.
        return " ".join(self.detailed_description)

    @property
    def searchable_text(self) -> str:
        """Text blob used for embedding: title + abstract + claims (the legally operative text).
        Detailed description is deliberately excluded from the default embedding — it's long,
        repetitive boilerplate in patent filings and dilutes the signal from title/abstract/claims.
        It's still stored on the record and available to display or search over separately.
        """
        parts = [self.title, self.abstract, self.claims_text]
        return " ".join(p for p in parts if p)


# Strip out empty/non-string entries from a raw `detailed_description` list (see module
# docstring — ~50% of entries in the sample data are empty-string XML-parsing artifacts).
def _clean_description(paragraphs: list) -> list[str]:
    return [p.strip() for p in (paragraphs or []) if isinstance(p, str) and p.strip()]


# Load every patents_ipa*.json file in `data_dir`, normalize records, and return
# (patents, stats) where stats reports how many records were excluded and why.
def load_patents(data_dir: str) -> tuple[list[Patent], dict]:
    files = sorted(glob.glob(os.path.join(data_dir, "patents_ipa*.json")))
    patents: list[Patent] = []
    stats = {"files": len(files), "total_records": 0, "excluded": 0, "excluded_reasons": {}}

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            raw_records = json.load(f)
        stats["total_records"] += len(raw_records)

        for rec in raw_records:
            missing = [k for k in REQUIRED_FIELDS if not rec.get(k)]
            if missing:
                stats["excluded"] += 1
                reason = ",".join(missing)
                stats["excluded_reasons"][reason] = stats["excluded_reasons"].get(reason, 0) + 1
                continue

            patents.append(
                Patent(
                    doc_number=rec["doc_number"],
                    title=rec["title"],
                    abstract=rec["abstract"],
                    classification=rec["classification"],
                    claims=[c for c in (rec.get("claims") or []) if isinstance(c, str) and c.strip()],
                    detailed_description=_clean_description(rec.get("detailed_description")),
                    filename=rec.get("filename", ""),
                    source_file=os.path.basename(fp),
                )
            )

    return patents, stats


if __name__ == "__main__":
    import sys

    data_dir = sys.argv[1] if len(sys.argv) > 1 else "patent_data_small"
    pats, stats = load_patents(data_dir)
    print(f"Loaded {len(pats)} patents from {stats['files']} files "
          f"({stats['excluded']} excluded of {stats['total_records']} total records).")
    if stats["excluded_reasons"]:
        print("Exclusion reasons:", stats["excluded_reasons"])
    print("Example record:", pats[0])
