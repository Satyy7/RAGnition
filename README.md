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
