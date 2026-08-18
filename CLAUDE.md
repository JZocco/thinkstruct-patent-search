# Thinkstruct Interview Take-Home — Patent Claim Search

## What this is

A take-home coding project for a Thinkstruct interview (2 hours suggested, spread across
3 parts). Full instructions are in `thinkstruct-coding-task.pdf` (project root) — treat that
PDF as the source of truth for requirements; this file is working notes + decisions so future
sessions don't re-derive context.

**Framing to keep in mind while building:** the brief explicitly says the goal is not
speed-coding but demonstrating critical thinking about search/IR and system design. Prefer a
simple, well-reasoned solution with clear commentary over a maximal feature set. Don't gold-plate
any one part at the expense of the others — Part 1 and Part 2 are required-ish, Part 3 is
explicitly "pick 1-2, as time allows."

## Deliverables checklist (what "done" looks like)

- [x] Part 1: search engine (basic semantic search + hybrid constraints) — `src/search.py`
- [x] Part 1: timing comparison of hybrid-on vs hybrid-off, with commentary on scaling it —
      `src/benchmark_hybrid.py` → generates directly into `README.md`'s Part 1 section (moved
      out of a standalone `notes/timing_results.md` file — see Part 1 build notes below for why)
- [x] Part 2: `SYSTEM_DESIGN.md` — scale-up design doc, intern-readable, includes components,
      pipelines, rough cost breakdown, error handling, monitoring, known challenges
- [x] Part 2: one small proof-of-concept piece of that system — `poc/` (SQLite pipeline status
      tracking + dashboard; see "Part 2 build notes" below for why this isn't the Docker/Postgres
      PoC originally planned)
- [x] Part 3: chosen enhancement(s) — two-phase re-rank (`src/rerank.py`) + eval/fine-tune
      pipeline (`src/eval.py` → generates directly into `README.md`'s Part 3 section, moved out
      of a standalone `notes/eval_results.md` file, same pattern as Part 1's benchmark — see
      "Decisions" below)
- [x] `README.md` covering: problem statement chosen, how the code addresses it, how to run
      Parts 1-3 reproducibly — **keep this updated alongside the code** (user preference, not
      write-once-at-the-end)
- [ ] ~2 min screen recording demoing the features (record last, once the CLI is stable)
- [ ] Ship as a GitHub link or zip

## Decisions made (so we don't re-litigate)

- **Embeddings:** local `sentence-transformers` (e.g. `all-MiniLM-L6-v2` to start; upgrade to a
  larger/patent-domain model only if time and quality justify it). Chosen over API embeddings so
  the reviewer can reproduce results with no API key and no cost.
- **Interface:** CLI (`python search.py --query "..." [--classification B60B...] [--title ...] ...`).
  Chosen to maximize time on search quality/Part 2/Part 3 rather than UI polish. Still needs to be
  clean enough to look good in the screen recording.
- **Part 3 picks (2) — both DONE, see build notes below for what changed from this plan:**
  1. **Two-phase search / re-ranking** (`src/rerank.py`) — semantic search narrows to a pool
     (default 50), then `cross-encoder/ms-marco-MiniLM-L-6-v2` re-ranks to top-k. Wired into
     `search.py` via `--rerank` / `--rerank-pool`. No extra data needed.
  2. **Evaluation & training pipeline** (`src/eval.py`) — used (abstract, claims) pairs from the
     existing data as positives (no hand-built negatives needed — `MultipleNegativesRankingLoss`
     uses in-batch items as negatives, matching the brief's note that positives are far easier to
     generate). 80/20 train/eval split, baseline eval → fine-tune → re-eval, real measured
     improvement (recall@1 0.875→0.945, MRR 0.910→0.969) — see README.md's Part 3 section.
  - Explicitly **not** doing: efficiency-at-scale and machine translation, since both require
    requesting additional data samples from Thinkstruct that we don't have.
- **Part 2 POC — actual outcome (superseded the original plan):** the original plan was a
  Dockerized FastAPI + Postgres/pgvector PoC. When it came time to build it, free disk had dropped
  to ~1.4-1.7GB and the Docker daemon wasn't even running — pulling images in that state risked
  repeating the disk-full incident right as we were mid-build. User chose (via AskUserQuestion) to
  skip Docker and do a lighter PoC instead: `poc/pipeline_poc.py` + `poc/dashboard.py`, a
  SQLite-backed (stdlib only, no installs) ingestion-pipeline status tracker + dashboard, run
  against the real 640-patent dataset. Same underlying idea as the Postgres component in
  `SYSTEM_DESIGN.md` (indexed metadata table + status column + dead-letter table), much lower
  footprint. Revisit Docker for this if disk space genuinely frees up and there's a reason to.

## Data

Location: `patent_data_small/` — 64 files named `patents_ipa{YYMMDD}.json`, one per filing week,
Feb 2024 onward. 640 patent records total, all vehicle-related (classification prefix `B60`).
`prettyprint/` has 2 of the same files reformatted for human reading — not a separate dataset,
ignore for code, just useful to eyeball structure.

**Actual JSON schema per record** (verified by inspection — note this differs from the field
*names* used in the PDF prose, which are capitalized/loosely described):

```json
{
  "title": "SPOKE",
  "doc_number": "20240051333",
  "filename": "US20240051333A1-20240215.XML",
  "abstract": "A spoke includes an axle body...",
  "detailed_description": ["paragraph 1 text", "", "paragraph 3 text", ...],
  "claims": ["1. An axle body, having...", "2. The spoke according to claim 1, wherein...", ...],
  "bibtex": "@patent{20240051333,\n    title = {SPOKE},\n    ...}",
  "classification": "B60B104FI"
}
```

Field mapping vs. the PDF's description: Title→`title`, Document Number→`doc_number`,
Abstract→`abstract`, Detailed Description→`detailed_description` (list of paragraph strings),
Claims→`claims` (list of claim strings, claim 1 typically has no leading number, later claims are
prefixed like `"2 . The spoke according to claim 1 , wherein..."`), Bibtext Citation→`bibtex`,
Classification Code→`classification`. There's also a `filename` field (source XML filename) not
mentioned in the PDF — harmless, can ignore or keep as provenance metadata.

**Data quality notes (checked across all 64 files):**
- No missing/null/empty top-level fields across the whole small dataset (`title`, `doc_number`,
  `abstract`, `classification`, etc. are always present and non-empty) — so the "some patents may
  be missing fields, exclude or handle separately" caveat in the brief doesn't actually bite on
  this sample. **Still write the handling code defensively** (skip/flag records missing required
  fields) since the brief asks us to document the choice, and larger real data almost certainly
  won't be this clean.
  - **This is the concrete thing to point at in the README** for "how missing fields are handled":
    document that the small sample turned out to be complete, and that the code path exists but
    was effectively untested against real gaps — call that out as a known limitation, don't
    overclaim it was validated.
- `detailed_description` lists are noisy: roughly half the entries are empty strings (`""`),
  presumably paragraph/whitespace artifacts from XML parsing. Filter these out before
  embedding/indexing (`[p for p in detailed_description if p.strip()]`).
- `classification` values seen: `B60C` (318), `B60B` (298), `B60D` (20), plus single-digit counts
  of `B60F`/`B60G`/`B60R`/`B60J`. All start with `B60` (road vehicles), consistent with the "2024+
  vehicle patents" framing. This is a good real example for the hybrid-search classification-prefix
  filter (the brief's own example, "starts with B60B", is directly testable against this data —
  ~298 matches).
- `claims[0]` is the independent claim; later claims often reference earlier ones ("The spoke
  according to claim 1...") — worth keeping claim numbering/dependency in mind if doing
  claim-to-claim mapping, though not required.

## Environment

- Git repo initialized at `/Users/zocco/Downloads/data` (`git init` done).
- **Python env: `.venv/` (plain venv on system Python 3.9.6), NOT conda.** A conda env was tried
  first but the machine's disk filled up completely (0 bytes free — see "Disk space incident"
  below) partly because of it; it was removed and a lean `venv` + `pip install --no-cache-dir` was
  used instead to minimize footprint. Activate with `source .venv/bin/activate` from repo root.
  Installed: `sentence-transformers==5.1.2`, `torch==2.8.0`, `numpy==2.0.2`, `scikit-learn==1.6.1`,
  plus `datasets==4.5.0` and `accelerate==1.10.1` (added for Part 3 — `SentenceTransformer.fit()`
  requires both; each cost <200MB, cheap enough not to worry about) (all pinned in
  `requirements.txt`). Total install footprint ≈ 1.2GB.
- **`SentenceTransformer` is pinned to `device="cpu"` explicitly** (see `src/index.py`) — on this
  Mac, the default MPS (Metal) backend tried to cache compiled graphs to `/tmp` and errored out
  under low disk space. CPU is plenty fast for 640 patents and sidesteps that entirely.
- `docker` CLI is present (27.5.1) — usable for the Part 2 POC. Confirm the Docker daemon is
  actually running before relying on it (`docker ps`).
- `node` v23.10.0 is available but not expected to be needed (pure Python project).
- **Known machine-specific bug: `float32` matmul silently returns `NaN` on CPU for at least the
  `cross-encoder/ms-marco-MiniLM-L-6-v2` model** (PyTorch 2.8.0 CPU path via Apple's Accelerate
  BLAS backend on this Mac). Verified by tracing forward-pass activations layer by layer —
  embeddings are fine, the very first attention `Linear` already returns `NaN` on clean,
  non-`NaN`/non-`Inf` weights and inputs; basic isolated ops (`LayerNorm`, `scaled_dot_product_attention`,
  `Linear`+`GELU` on random tensors) do NOT reproduce it, so it's not a blanket torch-on-this-Mac
  bug — it's specific to this model/shape combination (the bi-encoder from Part 1 never showed
  this). Fix: cast the model to `float64` (`model.model = model.model.double()` for a
  `CrossEncoder`) — see `src/rerank.py`. If a similar unexplained-`NaN` issue shows up with a
  *different* model later in this project, try the same fix first, but verify with the same
  layer-by-layer tracing approach rather than assuming it's the identical root cause.

### Disk space incidents — watch for this again, alert user if free space drops toward ~500MB

Two separate incidents so far, both on this same machine:
1. **(Initial Part 1 setup)** Boot volume ran completely out of space mid-build (installing the
   original conda env + downloading model weights pushed it to 0 bytes free, breaking even basic
   shell commands). Fixed by removing the conda env, `conda clean --all`, clearing
   `~/.cache/pip`/`huggingface`/`torch`, and switching to a lean `venv`.
2. **(Part 3 fine-tuning run)** Free space dropped from ~2.0GB to 245MB during/after an
   `eval.py` run. Investigated: the empty `src/checkpoints/model/` dir the HF `Trainer` created
   (default `output_dir`, despite `eval.py` never calling `.save()`) was 0 bytes — NOT the cause.
   No large new files were found anywhere with `find ... -newer requirements.txt -size +5M`. Best
   read: **something else on the user's Mac is independently consuming disk in the background**
   (same pattern as a separate ~4.0GB→1.4GB drop that happened between Part 2 sessions with no
   disk-heavy work running at all) — not something this project's code is causing. Don't assume
   every low-disk reading is our fault; check for recently-created large files before concluding
   that.

Current free space fluctuates in roughly the **0.2–4GB range** on this machine, independent of
what this project is doing — there isn't a lot of headroom and it isn't fully explained.
Concretely, going forward:
- Prefer `pip install --no-cache-dir` over letting pip cache wheels.
- Avoid conda for anything else in this project; venv's footprint is smaller and easier to fully
  remove.
- `SentenceTransformer.fit()` (used in `eval.py`) pulls in `datasets` + `accelerate` and creates a
  `checkpoints/` dir via the HF `Trainer` even though we never call `.save()` — check for and
  clean up empty/stale `checkpoints/` dirs after training runs.
- Check `df -h /` before/after any step that installs packages, downloads model weights, or runs
  training — and don't hesitate to pause and ask the user (via AskUserQuestion) rather than
  guessing when free space is critically low, per their explicit instruction.
- The user's `~/Downloads`, `~/Library/Developer` (Xcode), and Docker Desktop data are known
  large, user-owned items from earlier investigation — don't delete these ourselves; point the
  user to Storage Management (Apple menu → About This Mac → Storage → Manage) if more space is
  needed, or just ask them to check what's eating space (worked the second time).

## Repo structure (current — Parts 1, 2, and 3 all done)

```
data/
├── thinkstruct-coding-task.pdf      # original brief
├── CLAUDE.md                        # this file
├── README.md                        # deliverable — DONE for Parts 1-3
├── SYSTEM_DESIGN.md                 # Part 2 write-up. DONE.
├── patent_data_small/               # given data
├── prettyprint/                     # given, reference only
├── requirements.txt                 # pip-installable, pinned versions (see Environment)
├── .gitignore                       # .venv/, __pycache__/, notes/index_cache.pkl, poc/pipeline.db
├── .venv/                           # gitignore this — local env, not committed
├── src/
│   ├── ingest.py                    # load JSON files → normalized records, filter bad data. DONE.
│   ├── index.py                     # PatentIndex: embeddings + adaptive hybrid filtering. DONE.
│   ├── search.py                    # CLI entrypoint: semantic + hybrid + --rerank + --show. DONE.
│   ├── benchmark_hybrid.py          # Part 1 timing deliverable → writes into README.md directly. DONE.
│   ├── rerank.py                    # Part 3: cross-encoder two-phase re-ranking. DONE.
│   └── eval.py                      # Part 3: eval + fine-tune pipeline → writes into README.md directly. DONE.
├── poc/                             # Part 2 proof-of-concept. DONE (SQLite, not Docker — see below).
│   ├── pipeline_poc.py              # runs real 640 patents through simulated pipeline stages
│   ├── dashboard.py                 # status dashboard reading poc/pipeline.db
│   ├── pipeline.db                  # gitignore this — regenerate via pipeline_poc.py
│   └── README.md                    # PoC-specific docs (what/why/limitations)
└── notes/
    ├── index_cache.pkl              # cached embeddings (gitignore — regenerate via search.py)
    └── demo_script.md               # screen recording script
```

**Note:** Part 1 hybrid timing results used to live in a standalone `notes/timing_results.md` —
moved into `README.md` directly (between `<!-- BENCHMARK_RESULTS:START -->`/`END` markers,
regenerated by `benchmark_hybrid.py`) per user request, so the reviewer-facing README is the
single source of truth instead of duplicating a condensed version in the README and the full
version in a separate file. See Part 1 build notes below.

## Part 1 build notes (things that changed from the original plan)

- The hybrid-search "pre-filter before vector search" idea from the original Decisions section
  turned out to be **not unconditionally faster** — measured in `benchmark_hybrid.py`'s output:
  numpy fancy-indexing (`embeddings[mask]`) to pre-filter incurs an O(N) copy, which can cost as
  much as the "wasted" work it was meant to save when the filter isn't very selective.
  `PatentIndex.search()` in `src/index.py` is now **adaptive**: it pre-filters only when the
  metadata filter's measured selectivity is below `PatentIndex.PREFILTER_SELECTIVITY_THRESHOLD`
  (0.20, roughly/empirically chosen — see the class docstring), otherwise it runs the dense
  similarity matmul over everything and filters the sorted output, same as the no-filter path.
  This is the more interesting and more honest version of the "commentary on efficiency" the
  brief asks for.
- `searchable_text` (what actually gets embedded) = title + abstract + claims. Detailed description
  is deliberately excluded from the embedding (long/repetitive/boilerplate) but still stored and
  shown via `--show`.
- **Later change (user request):** `benchmark_hybrid.py` originally wrote its table + commentary
  to a standalone `notes/timing_results.md`, with only a condensed summary duplicated into
  README.md. User asked to consolidate into the README directly instead, reasoning that the
  README is the actual reviewer-facing deliverable per the brief, and having a short version in
  README + a long version in a separate file created a "which one is authoritative" duplication.
  Implemented via HTML comment markers (`<!-- BENCHMARK_RESULTS:START/END -->`) in README.md's
  Part 1 section — `write_report()` now splices its generated content between those markers
  in-place rather than writing a separate file, so rerunning the script still regenerates real
  (not hand-typed) results, they just land directly in the README now. `notes/timing_results.md`
  was deleted; all other references to it (in `index.py`'s comments, `notes/demo_script.md`) were
  updated to point at the README section instead.
- **Follow-up simplification (user request):** removed the `commentary()` function from
  `benchmark_hybrid.py` entirely. Rationale: unlike the table (which genuinely changes based on
  the measured numbers each run), the analysis prose was a static string that never varied — code
  generating fixed text isn't really "generation," it's just an indirect way to store
  documentation. The `<!-- BENCHMARK_RESULTS:START/END -->` markers in README.md now wrap ONLY
  the table; the analysis paragraphs live immediately after the markers as regular hand-written
  README prose (edit them directly, same as any other section). `write_report()` in
  `benchmark_hybrid.py` only ever touches what's between the markers, so this is safe — rerunning
  the script updates the table and leaves the analysis prose untouched.

## Part 2 build notes (things that changed from the original plan)

- Planned PoC (Dockerized FastAPI or Postgres/pgvector) was dropped mid-build when free disk hit
  ~1.4-1.7GB and the Docker daemon was found not running — pulling images in that state was too
  risky given the earlier full-disk incident. Asked the user via AskUserQuestion; they chose a
  lighter, Docker-free PoC. Built `poc/pipeline_poc.py` + `poc/dashboard.py` instead: SQLite
  (stdlib, zero new installs) ingestion-pipeline status tracking + dashboard, run against the
  real 640-patent dataset. See `poc/README.md` for full details/limitations.
- The real data has 0 naturally-failing records (consistent with Part 1's ingest findings), so the
  PoC's dead-letter demo uses a small **simulated** failure rate (3%, fixed seed) at the
  "embedding" stage — clearly logged as simulated in both the code and `poc/README.md`, not
  presented as a real failure mode.
- `SYSTEM_DESIGN.md`'s sharding recommendation (partition the vector index by classification
  prefix) is directly justified by Part 1's measured finding that classification filters can be
  extremely selective (down to 0.16% in the sample) — worth pointing this connection out if asked
  about it, since it shows Part 1 and Part 2 aren't just adjacent, one motivates the other.

## Part 3 build notes (things that changed from the original plan)

- **The `float32` NaN bug** (see Environment section above) ate a good chunk of the rerank
  implementation time — worth remembering the debugging path that worked (trace hidden states
  layer by layer via `output_hidden_states=True`, then step into the failing layer's submodules
  manually) if something similar happens again, rather than assuming it's a code bug in
  `rerank.py`/`eval.py` itself.
- `model.fit()` (sentence-transformers' legacy training API, which Part 3 uses) needed two
  additional packages not anticipated in the original plan: `datasets` and `accelerate>=0.26.0`
  (it wraps the modern HF `Trainer` internally). Both installed cheaply (<200MB each). It also
  silently creates a `checkpoints/` output dir even without calling `.save()` — turned out to be
  empty/harmless here, but check for it after training runs.
- **Second disk incident happened here** (free space 2.0GB → 245MB during the `eval.py` run) — see
  the Disk space incidents section above. Investigated and did NOT find evidence it was caused by
  our code; paused and asked the user rather than guessing, per their standing instruction.
- Chose (abstract, claims) as the positive-pair task for eval/fine-tuning rather than
  (title, abstract) or something involving `detailed_description` — abstract and claims are both
  substantive, non-overlapping-in-wording descriptions of the same invention, and neither is the
  trivial "record contains itself" case using `index.py`'s `searchable_text` (which already
  concatenates title+abstract+claims together) would have been.
- Real, single-run results (not tuned/cherry-picked): recall@1 0.875→0.945, recall@5 0.953→1.000,
  recall@10 0.984→1.000, MRR 0.910→0.969 on a 128-patent held-out eval set. Better than the
  brief's "no expectation of significant improvement" baseline — plausible given the task
  (abstract→claims of the same patent) is a relatively easy semantic match for the base model to
  begin with, not necessarily replicable on a harder/larger real corpus.
- **Later change (user request):** moved `notes/eval_results.md` into README.md's Part 3 section,
  same pattern as the Part 1 benchmark move (`<!-- EVAL_RESULTS:START/END -->` markers wrapping
  just the metrics table; the methodology description and "Honest caveats" bullets became static
  README prose right after, since they don't change run to run). `write_report()` in `eval.py`
  now splices only the table between the markers. `notes/eval_results.md` was deleted; other
  references to it (README's "How to run" comment, `notes/demo_script.md`) were updated to point
  at the README section instead.

## Conventions while working on this

- **Update `README.md` alongside code changes, not as a final write-up step** (explicit user
  preference) — whenever a new part/feature lands, add its "how to run" section and problem-
  statement framing to the README in the same pass, and verify every command shown actually runs
  as written (e.g. via `--help` diffed against the README, or by actually running the examples).
- Keep Part 1's hybrid-search timing comparison as an actual reproducible script/output (currently:
  generated directly into README.md by `benchmark_hybrid.py`), not just a claimed number — the brief
  explicitly asks for measured timing plus commentary on efficient implementation (e.g. pre-filter
  by classification/metadata before vector search, rather than embedding-search-then-filter).
- `SYSTEM_DESIGN.md` should be readable by an intern with no prior context — favor concrete
  component names and a rough cost number over abstract descriptions. It's fine (encouraged, per
  the brief) to pick something simple and explicitly call out its weaknesses rather than aim for a
  "perfect" architecture.
- The Part 2 POC code is allowed to be fast-and-sloppy/under-documented per the brief — don't
  over-invest polish there relative to Part 1/3.
- When in doubt about scope, re-check against the "Deliverables checklist" above rather than
  expanding further.
