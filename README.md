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

The embedding chain is deliberately a single model: vectors from different models
cannot share an index. When every reranker fails, results keep their fused order
and are marked `degraded`.
