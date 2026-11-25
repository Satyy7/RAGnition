"""Freeze data/eval/scenarios_test.yaml (run ONCE, then commit before M6 scoring code).

    uv run python scripts/freeze_test_set.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from ragnition.data.paths import EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH, WORLD_PATH
from ragnition.data.world import load_all_eval, load_world
from ragnition.eval.freeze import freeze


def main() -> int:
    evalset = load_all_eval(EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH)
    evalset.validate_against(load_world(WORLD_PATH))  # never freeze a broken set
    test = evalset.split("test")
    digest = freeze(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"froze {len(test)} test scenarios ({sum(s.answerable for s in test)} answerable)  sha256={digest}")
    print("Now commit scenarios_test.yaml + scenarios_test.lock on their own, before any M6 scoring code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
