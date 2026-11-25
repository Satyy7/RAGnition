"""Freeze the held-out test set, so 'we did not tune on it' is checkable.

The lock file stores the SHA-256 of scenarios_test.yaml. It is written once,
committed together with the test set BEFORE any M6 scoring code exists, and every
benchmark run verifies it. Changing the test set afterwards fails loudly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ragnition.data.paths import EVAL_TEST_LOCK_PATH, EVAL_TEST_SCENARIOS_PATH


class FrozenSetError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    # Normalise line endings: the hash must not depend on git's autocrlf setting.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def freeze(frozen_at: str, scenarios: Path = EVAL_TEST_SCENARIOS_PATH, lock: Path = EVAL_TEST_LOCK_PATH) -> str:
    if lock.exists():
        raise FrozenSetError(f"{lock.name} already exists; the test set is frozen once")
    digest = file_sha256(scenarios)
    lock.write_text(json.dumps({"file": scenarios.name, "sha256": digest, "frozen_at": frozen_at}, indent=2) + "\n",
                    encoding="utf-8")
    return digest


def verify(scenarios: Path = EVAL_TEST_SCENARIOS_PATH, lock: Path = EVAL_TEST_LOCK_PATH) -> str:
    if not lock.exists():
        raise FrozenSetError("test set is not frozen: run scripts/freeze_test_set.py")
    expected = json.loads(lock.read_text(encoding="utf-8"))["sha256"]
    actual = file_sha256(scenarios)
    if actual != expected:
        raise FrozenSetError(f"{scenarios.name} changed after freezing (expected {expected[:12]}, got {actual[:12]})")
    return actual
