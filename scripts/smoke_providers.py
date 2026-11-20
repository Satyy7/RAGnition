"""Live smoke test: one tiny real call per role (or per model) to check keys and model IDs.

Usage:
    uv run python scripts/smoke_providers.py                # one call per role, via fallback
    uv run python scripts/smoke_providers.py --each-model   # one call to EVERY chat model
    uv run python scripts/smoke_providers.py --no-cache     # force real network calls
    uv run python scripts/smoke_providers.py --list-models  # model IDs Groq/Gemini/OpenRouter serve now
    uv run python scripts/smoke_providers.py --pin gemini:gemini-3.8-flash   # debug one model

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


def show_failure(label: str, attempts) -> None:
    print(f"  [FAIL] {label}")
    for a in attempts:
        print(f"         {a.slot}: {a.outcome}  {a.detail[:250]}")


async def list_models(gw: ModelGateway) -> None:
    """Ask each OpenAI-compatible provider which model IDs it currently serves."""
    for name, provider in gw.config.providers.items():
        key = gw.settings.secret(provider.api_key_setting)
        if provider.kind != "openai_compat" or key is None:
            continue
        url = f"{provider.base_url.rstrip('/')}/models"
        resp = await gw.http.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=30)
        if not resp.is_success:
            print(f"\n{name}: HTTP {resp.status_code} {resp.text[:200]}")
            continue
        ids = sorted(m.get("id", "?") for m in resp.json().get("data", []))
        print(f"\n{name} ({len(ids)} models):")
        for model_id in ids:
            print(f"  {model_id}")


def show(label: str, resp) -> None:
    trail = " -> ".join(f"{a.slot}:{a.outcome}" for a in resp.attempts) or "(cached)"
    print(f"  [PASS] {label}: {resp.provider}:{resp.model} {resp.latency_ms:.0f}ms cached={resp.cached}  trail: {trail}")


async def main(each_model: bool, use_cache: bool, models: bool, pin: str | None) -> int:
    settings = get_settings()
    # Single-model debugging shows every retry as it happens; otherwise stay quiet
    # and print failures with full detail at the end.
    configure_logging("INFO" if pin else "ERROR", json_output=False)
    failures = 0

    async with ModelGateway.from_settings(settings) as gw:
        if models:
            await list_models(gw)
            return 0
        if pin:
            role = next((r for r in ("generator", "judge") if pin in [s.id for s in gw.executor(r).slots]), None)
            if role is None:
                print(f"'{pin}' is not in the generator or judge chain")
                return 1
            print(f"Calling {pin} (no cache)... retries and timeouts are logged below.")
            try:
                resp = await gw.chat(PROMPT, role=role, use_cache=False, pin=pin)
                show(f"{pin} said {resp.text.strip()[:40]!r}", resp)
                return 0
            except AllProvidersExhausted as exc:
                show_failure(pin, exc.attempts)
                return 1
        print("API keys:")
        for name, provider in gw.config.providers.items():
            if provider.api_key_setting is None:
                state = f"not needed (models in {settings.model_cache_dir})"
            else:
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
                    show_failure(label, exc.attempts)

        print("\nEmbedding (first run downloads local models, ~1.5 GB, can take minutes):")
        try:
            resp = await gw.embed(["database connection pool exhaustion"], input_type="query", use_cache=use_cache)
            show(f"dims={resp.dimensions}", resp)
        except AllProvidersExhausted as exc:
            failures += 1
            show_failure("embedding", exc.attempts)

        print("\nRerank:")
        docs = [
            "The office coffee machine was replaced on Monday.",
            "Payment service timeouts were caused by an exhausted database connection pool.",
            "Kubernetes liveness probes restart unhealthy containers.",
        ]
        resp = await gw.rerank("why did the payment service time out?", docs, use_cache=use_cache)
        if resp.degraded:
            failures += 1
            show_failure("rerank (passthrough order used)", resp.attempts)
        else:
            show(f"best doc index={resp.results[0].index} (expected 1)", resp)

    print(f"\n{'All checks passed.' if failures == 0 else f'{failures} check(s) failed.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--each-model", action="store_true", help="test every chat model individually")
    parser.add_argument("--no-cache", action="store_true", help="bypass the call cache")
    parser.add_argument("--list-models", action="store_true", help="list model IDs each LLM provider serves")
    parser.add_argument("--pin", metavar="PROVIDER:MODEL", help="test one chat model only, with live retry logs")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.each_model, not args.no_cache, args.list_models, args.pin)))
