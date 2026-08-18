"""
CLI search engine over the vehicle patent corpus.

Examples:
  # plain semantic search over title+abstract+claims
  python search.py --query "spoke that resists axial tension"

  # hybrid: semantic query constrained to a classification prefix (e.g. wheels = B60B)
  python search.py --query "damping suspension" --classification B60B

  # hybrid: keyword-only search, no semantic query
  python search.py --title-contains "spoke"
  python search.py --abstract-keywords "tire" "sensor"

  # exact title lookup
  python search.py --title-exact "SPOKE"

  # show a full patent record (claims + description) by document number
  python search.py --show 20240051333

  # two-phase search: semantic recall -> cross-encoder re-rank (see rerank.py)
  python search.py --query "spoke that resists axial tension" --rerank
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from index import build_default_index


# Word-wrap `text` to `width` columns with a consistent left indent, for readable CLI output.
def wrap(text: str, width: int = 100, indent: str = "      ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


# Print one search hit: rank, score, doc number, title, classification, and an abstract snippet.
def print_result(rank: int, score: float, patent) -> None:
    print(f"[{rank}] score={score:.3f}  {patent.doc_number}  |  {patent.title}  |  {patent.classification}")
    print(wrap(patent.abstract[:280] + ("..." if len(patent.abstract) > 280 else "")))
    print()


# Print a full patent record (abstract, all claims, first few description paragraphs) — used by --show.
def print_full(patent) -> None:
    print("=" * 100)
    print(f"{patent.title}  ({patent.doc_number})  [{patent.classification}]")
    print(f"source file: {patent.source_file}")
    print("-" * 100)
    print("ABSTRACT:")
    print(wrap(patent.abstract))
    print()
    print(f"CLAIMS ({len(patent.claims)}):")
    for c in patent.claims:
        print(wrap(c))
        print()
    print(f"DETAILED DESCRIPTION ({len(patent.detailed_description)} paragraphs):")
    for p in patent.detailed_description[:3]:
        print(wrap(p[:400] + ("..." if len(p) > 400 else "")))
        print()
    if len(patent.detailed_description) > 3:
        print(f"      ... ({len(patent.detailed_description) - 3} more paragraphs omitted)")
    print("=" * 100)


# CLI entrypoint: parse args, load/build the index, run the requested search mode
# (--show a record / plain semantic / hybrid / --rerank), and print the results.
def main():
    ap = argparse.ArgumentParser(description="Search the vehicle patent corpus.", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--query", "-q", type=str, default=None, help="Natural language semantic query")
    ap.add_argument("--top-k", "-k", type=int, default=5, help="Number of results to return")
    ap.add_argument("--classification", type=str, default=None, help="Classification code PREFIX constraint, e.g. B60B")
    ap.add_argument("--title-contains", type=str, default=None, help="Substring the title must contain")
    ap.add_argument("--title-exact", type=str, default=None, help="Exact title match")
    ap.add_argument("--abstract-keywords", type=str, nargs="+", default=None, help="Keywords that must ALL appear in the abstract")
    ap.add_argument("--show", type=str, default=None, help="Print the full record for a given doc_number and exit")
    ap.add_argument("--rerank", action="store_true", help="Apply cross-encoder re-ranking on top of semantic recall (see rerank.py)")
    ap.add_argument("--rerank-pool", type=int, default=50, help="How many semantic candidates to pull before re-ranking")
    args = ap.parse_args()

    idx = build_default_index()

    if args.show:
        matches = [p for p in idx.patents if p.doc_number == args.show]
        if not matches:
            print(f"No patent with doc_number={args.show}", file=sys.stderr)
            sys.exit(1)
        print_full(matches[0])
        return

    if not any([args.query, args.classification, args.title_contains, args.title_exact, args.abstract_keywords]):
        ap.error("Provide at least one of --query / --classification / --title-contains / --title-exact / --abstract-keywords / --show")

    if args.rerank:
        from rerank import rerank_search
        results = rerank_search(
            idx,
            query=args.query,
            top_k=args.top_k,
            pool_size=args.rerank_pool,
            classification_prefix=args.classification,
            title_contains=args.title_contains,
            title_exact=args.title_exact,
            abstract_keywords=args.abstract_keywords,
        )
    else:
        results = idx.search(
            query=args.query,
            top_k=args.top_k,
            classification_prefix=args.classification,
            title_contains=args.title_contains,
            title_exact=args.title_exact,
            abstract_keywords=args.abstract_keywords,
        )

    if not results:
        print("No results.")
        return

    print(f"{len(results)} result(s):\n")
    for i, r in enumerate(results, 1):
        print_result(i, r.score, r.patent)


if __name__ == "__main__":
    main()
