"""Find which services an incident is about, from its raw text (alerts, logs, notes).

The alias dictionary is GENERATED from the service registry (world.yaml), never
hand-written per incident:
- the service id itself (`payments-api`);
- hyphenated names in its datastores (`cart-cache`, `payments-db`, `session-store`);
- proper names in its datastores and external providers (`PgBouncer`, `Paygate`,
  `CarrierHub`), and its runtime's product name (`CoreDNS`).
An alias that would point at more than one service (`PostgreSQL`, `Redis`, `Kafka`)
is dropped: an ambiguous name is not evidence. In a real deployment this table
would come from a service catalog / CMDB instead of world.yaml.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ragnition.data.world import World

_HYPHENATED = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b")
_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*\b|\b[A-Z][a-z]*[A-Z][A-Za-z]*\b")
# Generic words that look like names but identify nothing.
_GENERIC = {"the", "via", "max", "keys", "client", "server", "topic", "cluster", "api", "with", "and", "for",
            "only", "over", "account", "returns", "limit", "edge", "cache", "static", "assets", "images",
            "requests", "mutual", "pinned", "trust", "bundle", "small", "data", "nodes", "disk", "each",
            "indices", "carrier", "email", "consumer", "group", "partitions", "shared", "quotes", "labels",
            "retry", "after", "when", "exceeded"}


def _candidates(text: str) -> set[str]:
    found = {m.group(0).lower().rstrip("-") for m in _HYPHENATED.finditer(text)}
    found |= {m.group(0).lower() for m in _PROPER.finditer(text)}
    return {a for a in found if len(a) >= 3 and a not in _GENERIC and not a[0].isdigit()}


def build_aliases(world: World) -> dict[str, str]:
    """alias -> service id, unambiguous aliases only."""
    owners: dict[str, set[str]] = defaultdict(set)
    for s in world.services:
        owners[s.id].add(s.id)
        for text in [*s.datastores, *s.external]:
            for alias in _candidates(text):
                owners[alias].add(s.id)
        # Runtime: only its product name ('CoreDNS 1.11 (Kubernetes add-on)' -> 'coredns').
        runtime = s.language.split()[0].lower().removesuffix(".js")
        if len(runtime) >= 3 and runtime not in _GENERIC:
            owners[runtime].add(s.id)
    return {alias: next(iter(svcs)) for alias, svcs in owners.items() if len(svcs) == 1}


@dataclass(frozen=True)
class Mention:
    service: str
    alias: str
    count: int


class EntityLinker:
    def __init__(self, world: World) -> None:
        self.aliases = build_aliases(world)
        # Longest alias first, so 'inventory-reservations' wins over a shorter overlap.
        names = sorted(self.aliases, key=len, reverse=True)
        # Boundaries: not a letter/digit. Hyphen and dot ARE boundaries, so 'coredns-6f9b8-2xk4p'
        # and 'api.paygate.example' still match 'coredns' and 'paygate'.
        self.pattern = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, names)) + r")(?![a-z0-9])")

    def link(self, text: str) -> list[Mention]:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for m in self.pattern.finditer(text.lower()):
            counts[self.aliases[m.group(1)]][m.group(1)] += 1
        return sorted((Mention(svc, max(a, key=a.get), sum(a.values())) for svc, a in counts.items()),
                      key=lambda m: (-m.count, m.service))

    def services(self, text: str) -> list[str]:
        return [m.service for m in self.link(text)]
