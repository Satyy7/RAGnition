"""Live smoke test: one tiny real call per role (or per model) to check keys and model IDs.

Usage:
    uv run python scripts/smoke_providers.py                # one call per role, via fallback
    uv run python scripts/smoke_providers.py --each-model   # one call to EVERY chat model
    uv run python scripts/smoke_providers.py --no-cache     # force real network calls

Costs a handful of free-tier requests. Never prints key values.
"""

from __future__ import annotations

import argparse
import asyncio

from ragnition.core.config import get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import AllProvidersExhausted, ChatMessage, ChatRequest, ModelGateway

PROMPT = ChatRequest(
    messages=[ChatMessage(role="user", content="Reply with exactly the word: OK")],
    max_tokens=512,  # reasoning models spend tokens before answering
)


def show(label: str, resp) -> None:
    trail = " -> ".join(f"{a.slot}:{a.outcome}" for a in resp.attempts) or "(cached)"
    print(f"  [PASS] {label}: {resp.provider}:{resp.model} {resp.latency_ms:.0f}ms cached={resp.cached}  trail: {trail}")


async def main(each_model: bool, use_cache: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    failures = 0

    async with ModelGateway.from_settings(settings) as gw:
        print("API keys:")
        for name, provider in gw.config.providers.items():
            state = "set" if settings.secret(provider.api_key_setting) else "MISSING"
            print(f"  {name:<11} {state}")

        print("\nChat:")
        for role in ("generator", "judge"):
            pins = [s.id for s in gw.executor(role).slots] if each_model else [None]
            for pin in pins:
                label = f"{role}[{pin}]" if pin else role
                try:
                    resp = await gw.chat(PROMPT, role=role, use_cache=use_cache, pin=pin)
                    show(f"{label} said {resp.text.strip()[:20]!r}", resp)
                except AllProvidersExhausted as exc:
                    failures += 1
                    print(f"  [FAIL] {label}: {exc}")

        print("\nEmbedding:")
        try:
            resp = await gw.embed(["database connection pool exhaustion"], input_type="query", use_cache=use_cache)
            show(f"dims={resp.dimensions}", resp)
        except AllProvidersExhausted as exc:
            failures += 1
            print(f"  [FAIL] embedding: {exc}")

        print("\nRerank:")
        docs = [
            "The office coffee machine was replaced on Monday.",
            "Payment service timeouts were caused by an exhausted database connection pool.",
            "Kubernetes liveness probes restart unhealthy containers.",
        ]
        resp = await gw.rerank("why did the payment service time out?", docs, use_cache=use_cache)
        if resp.degraded:
            failures += 1
            print(f"  [FAIL] every reranker failed; passthrough order used. trail: {resp.attempts}")
        else:
            show(f"best doc index={resp.results[0].index} (expected 1)", resp)

    print(f"\n{'All checks passed.' if failures == 0 else f'{failures} check(s) failed.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--each-model", action="store_true", help="test every chat model individually")
    parser.add_argument("--no-cache", action="store_true", help="bypass the call cache")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.each_model, not args.no_cache)))
