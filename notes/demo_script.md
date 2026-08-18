# Screen recording script (~2 min, Parts 1-3)

## Before you hit record

- `cd` into the repo root, `source .venv/bin/activate`, then `cd src` — do this off-camera.
- Run any one search command once, off-camera, to warm the OS file cache (model weights are
  already downloaded from earlier runs, but a cold first call is a touch slower). E.g.:
  `python search.py --query "test" --top-k 1`
- Bump your terminal font size so it's readable in a recording.
- Have a second terminal tab/split ready with `SYSTEM_DESIGN.md` and `README.md` already opened
  in an editor (or just `cat` them) so you're not waiting on file-open lag on camera.

Total budget: ~120s. Times below are targets, not hard stops — better to talk a little fast than
to cut a section.

---

## 0:00–0:10 — Intro (say this while your terminal is on screen)

> "This is a hybrid patent search engine over the vehicle-patent dataset — semantic search plus
> metadata filters like classification code and title. I'll walk through all three parts."

## 0:10–0:55 — Part 1: search engine (45s)

Run, narrating briefly as each finishes:

```bash
# plain semantic search
python search.py --query "spoke that resists axial tension" --top-k 3
```
> "Plain natural-language search over title, abstract, and claims."

```bash
# hybrid: semantic + classification code constraint
python search.py --query "damping suspension system" --classification B60B --top-k 3
```
> "And this is hybrid search — same semantic query, but constrained to a classification code
> prefix, here vehicle wheels. It also supports title and abstract-keyword constraints."

```bash
sed -n '/Hybrid search + the efficiency finding/,/PatentIndex.search/p' ../README.md | head -25
```
> "I measured hybrid search with and without pre-filtering at scale — turns out pre-filtering
> isn't always faster, it depends on filter selectivity, so the search function adapts based on
> that. Full writeup is in the README."

## 0:55–1:20 — Part 2: system design + PoC (25s)

```bash
head -60 ../SYSTEM_DESIGN.md
```
> "For Part 2, this design doc covers how this would run at 10 million patents — ingestion
> pipeline, sharded vector index, cost breakdown, error handling."

```bash
python ../poc/dashboard.py
```
> "And this is a small proof-of-concept of one piece of that: a pipeline status dashboard,
> running against the real 640-patent dataset, tracking ingestion status and dead-lettered
> failures."

## 1:20–2:00 — Part 3: enhancements (40s)

```bash
python search.py --query "spoke that resists axial tension" --top-k 3 --rerank
```
> "For Part 3 I picked two enhancements. First, two-phase search — this re-ranks the semantic
> results with a cross-encoder for better precision, notice the ranking changed from the plain
> search earlier."

```bash
sed -n '/evaluation & fine-tuning pipeline/,/EVAL_RESULTS:END/p' ../README.md
```
> "Second, an evaluation and fine-tuning pipeline — I train the model to match a patent's abstract
> to its own claims text, and measured a real improvement: recall at 1 went from 87.5% to 94.5%
> after fine-tuning, on a held-out eval set."

## Closing (if you have a few seconds left)

> "Everything's reproducible from the README — thanks for watching!"

---

## Notes

- If you're short on time, the safest cut is trimming the Part 2 `SYSTEM_DESIGN.md` `head` output
  narration down to one sentence — the dashboard run is the more visually interesting part.
- If a command runs long enough to feel awkward on camera, just talk over the wait — none of the
  commands above should take more than a couple seconds once caches are warm.
