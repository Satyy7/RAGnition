# RAGnition

**Intelligent Retrieval. Verified Evidence. Trustworthy Answers.**

An incident-intelligence and root-cause-analysis assistant built as an advanced RAG
engineering project. It is read-only decision support: it never changes production
systems.

> Status: M1 (project setup + model provider layer). Built milestone by milestone.

## Requirements

- Python 3.12 (managed by `uv`)
- [uv](https://docs.astral.sh/uv/)
- Free-tier API keys: Groq and Gemini (required); Jina and Cohere (recommended);
  OpenRouter (optional)

No paid API is required. In free-tier mode, use only public and synthetic data:
free tiers may use your inputs to improve their services.

## Setup

```powershell
uv sync                      # create .venv and install locked dependencies
Copy-Item .env.example .env  # then put your keys in .env
uv run pytest                # offline tests (no network, no keys needed)
uv run python scripts/smoke_providers.py   # live check of keys and model IDs
```

## Layout

```
config/providers.yaml          model chains, limits, resilience settings
backend/ragnition/core/        config, logging, model provider layer
backend/tests/                 offline unit tests
scripts/                       developer scripts
```

## Model provider layer

Every LLM, embedding and rerank call goes through `ModelGateway`, which provides:

- an ordered **fallback chain** per role (`generator`, `judge`, `embedding`, `rerank`);
- **client-side rate limiting** (token buckets for requests/min and tokens/min);
- **retries** with exponential backoff and jitter for transient errors;
- **circuit breakers**, so exhausted models are skipped until their cooldown ends;
- a **SQLite call cache**, so repeated identical calls are free;
- an **attempt trail** on every response that records which model actually answered.

Model placement: small encoder models run **locally** (CPU, ONNX via `fastembed`):
`BAAI/bge-base-en-v1.5` for embeddings and `BAAI/bge-reranker-base` for reranking.
Large generative models (generator, judge) use free-tier **APIs**. Local models are
downloaded once (~1.5 GB) into `MODEL_CACHE_DIR`.

The embedding chain is deliberately a single model: vectors from different models
cannot share an index. When every reranker fails, results keep their fused order
and are marked `degraded`.

## Data (M2)

```powershell
uv run python scripts/build_synthetic.py   # Kestrel Commerce corpus + eval files (~19 Groq calls)
uv run python scripts/fetch_sources.py     # public sources into data/raw (gitignored)
uv run python scripts/data_report.py       # licenses, integrity and leakage audit
```

The corpus has two parts:

- **Kestrel Commerce (synthetic).** A fictional retailer: 9 services, a change log,
  10 past postmortems and a runbook per service. The facts are hand-written in
  `data/synthetic/world.yaml`; an LLM only writes the prose. Every document is
  labelled `SYNTHETIC`.
- **Public references.** Kubernetes docs (CC BY 4.0), Google SRE book chapters
  (CC BY-NC-ND 4.0, local use only) and public company postmortems (all rights
  reserved, local use only). Raw files are never committed.

`data/eval/` holds 12 new incidents and their true root causes (10 answerable,
including multi-hop ones, and 2 with no evidence, where the right answer is
"not enough evidence"). The answers are never in the corpus: the corpus holds the
evidence (change records, similar past incidents, service notes) and RAGnition
has to connect it. `ensure_corpus_path` refuses any file under `data/eval`, and
`data_report.py` checks that no corpus document contains an answer.

`data/manifest.jsonl` records every document's source, license, redistribution
flag and SHA-256.

## Chunking (M3)

```powershell
uv run python scripts/chunk_corpus.py                      # parse + chunk with all strategies
uv run python scripts/chunk_corpus.py --show PM-2025-007   # inspect one document's chunks
```

HTML is reduced to its main article with `trafilatura`; Markdown is cleaned and
split at headings. Three strategies are written to `data/processed/` (gitignored):
`fixed` (384 tokens, 64 overlap), `structure` (heading sections, up to 384 tokens)
and `parent_child` (256-token children for search, 1,024-token parents for the
LLM). Sizes are counted with the BGE tokenizer itself, and every chunk starts with
a context header (document title and heading path). M5 compares the strategies.

## Search (M4)

```powershell
uv run python scripts/build_index.py                     # embed all strategies into Qdrant
uv run python scripts/search.py "payments timing out"     # dense and BM25 side by side
uv run python scripts/probe_retrieval.py                 # recall@k / MRR on the eval incidents
```

Dense vectors live in Qdrant **local mode** (`data/index/qdrant`, no server or
Docker; one process at a time). BM25 is implemented in
`backend/ragnition/retrieval/bm25.py` with an identifier-aware tokenizer
(`default_pool_size` is indexed whole and as `default`, `pool`, `size`) and is
rebuilt in memory when a `Retriever` opens.

## Retrieval benchmark (M5)

```powershell
uv run python scripts/generate_retrieval_queries.py     # ~200 queries, judge-filtered
uv run python scripts/chunk_corpus.py                   # all 8 configs in config/experiments.yaml
uv run python scripts/build_index.py                    # embeds only text not seen before
uv run python scripts/run_retrieval_benchmark.py --apply
```

Eight chunking configs (fixed / structure / parent-child at several sizes) are
compared with dense search (with and without BGE's query instruction) and BM25
on judge-checked synthetic queries plus the hand-labelled incidents. Results carry
95% bootstrap confidence intervals, and the winner is picked by a rule fixed
before the run: highest combined dense+BM25 Recall@10, and among statistically
tied configs the one with the fewest vectors. The choice is written to
`config/retrieval.yaml`; reports are in `data/eval/results/`.

Result (2026-11-24): all eight configs were statistically tied on Recall@10
(0.98-0.99, a ceiling effect: the generated queries are easy), so the rule picked
`fixed-510`, the cheapest (1,105 vectors vs up to 4,810). The query instruction
helped dense search slightly. The real bottleneck showed up in the incident slice:
root-cause change records are rarely retrieved, whatever the chunking.

## Change-aware retrieval (M6)

```powershell
uv run python scripts/run_m6_benchmark.py --split dev --apply   # tune on the 10 dev incidents
uv run python scripts/run_m6_benchmark.py --split test          # score the frozen test set once
```

Symptoms ("checkout 503s") share almost no words with the change that caused
them ("PgBouncer default_pool_size 60 -> 25"), so text search misses it. M6 adds
a retriever that works like an on-call engineer:

1. find the services in the incident text (alias table generated from the
   service registry; ambiguous names dropped);
2. walk the dependency graph (both directions, shared infrastructure adjacent to
   everything);
3. score changes before the incident: `proximity(hops) * recency(days) * risk(type)`,
   with a one-line explanation per suggestion;
4. fuse with BM25 and dense via reciprocal rank fusion, weighting BM25 up for
   identifier-heavy text.

**Evaluation protocol.** 30 held-out incidents (`data/eval/scenarios_test.yaml`)
were written and committed with their SHA-256 (`scenarios_test.lock`) before any
M6 scoring code existed. They are adversarial: newer distractor changes, culprits
1-2 hops from the symptoms, decoys whose words match the symptoms, changes dated
after the incident, rolled-back changes, culprits older than the time window. The
four parameters are grid-searched on the 10 dev incidents only; the test run
checks the hash and refuses to run twice without `--rerun` (every run is logged).
Pre-registered claim: on test, root-cause-change Recall@5 of the full system
beats adaptive RRF, with a paired 95% bootstrap CI above 0. It is compared against
a naive "newest change on the named services" baseline, not only against text search.

**Result (test run #1, 2026-11-25, 26 answerable held-out incidents):**

| system | root-cause change R@5 | root-cause MRR | nDCG@10 (graded) |
|---|---:|---:|---:|
| BM25 | 0.64 | 0.26 | 0.59 |
| RRF (BM25 + dense, adaptive) | 0.44 | 0.20 | 0.53 |
| RRF + newest changes on named services (naive) | 0.76 | 0.62 | 0.63 |
| **RRF + change-aware (full)** | **0.88** | **0.73** | **0.73** |

- Pre-registered claim supported: full - adaptive RRF on R@5 = +0.44, 95% CI [0.20, 0.68].
- Against the naive baseline: +0.12, CI [-0.08, 0.32], not significant overall. The
  gain is where the graph matters (1-hop culprits 0.88 vs 0.50; culprit never named
  2/2 vs 0/2; lexical decoys 5/5 vs 4/5), but those slices are small.
- Failure: both old-culprit incidents (44 and 50 days) are missed, because of the 21-day window,
  as designed. Slow-burn causes need another signal (e.g. trend detection).
- Negative result: adaptive fusion weighting hurt nDCG (-0.04, CI [-0.09, -0.01]);
  plain RRF is kept from M7 on. This decision was made after the test run, so it
  is not claimed as a measured improvement.

**Limits.** The test set and the scorer share an author; freezing first and the
adversarial cases reduce that bias but do not remove it. The data is synthetic and
the service registry is complete, which real CMDBs rarely are. The approach is
inspired by change-correlation features in incident-management tools; this
implementation and its numbers are our own.

## Reranking (M7)

```powershell
uv run python scripts/run_m7_benchmark.py --split dev --apply   # bake-off + tuning on dev
uv run python scripts/run_m7_benchmark.py --split test          # one test run
```

A cross-encoder judges whether a passage's *text* answers the query. That suits
postmortems, runbooks and docs, but not change records, whose wording rarely
matches the symptoms. Three ways of applying a reranker to the fused top-N are
compared: **naive** (reorder everything), **blend** (RRF of fused and reranked
order) and **slot-preserving** (change records keep their change-aware positions;
the reranker reorders only the explanatory evidence into the remaining slots).
Rerankers: local `BAAI/bge-reranker-base`, Jina `jina-reranker-v3`, Cohere
`rerank-v3.5`. Long incidents are condensed (alerts and error lines first,
near-duplicate log lines removed) to fit the cross-encoder's ~512-token input.

Pre-registered before any M7 run: (1) slot-preserving reranking improves graded
nDCG@10 over M6 retrieval (plain RRF), paired 95% CI above 0; (2) prediction:
naive reranking lowers root-cause Recall@5. Local is selected unless an API
reranker beats it by 0.02 nDCG on dev. This is the second milestone scored on the
frozen test set; its use is logged and counted in the report.
