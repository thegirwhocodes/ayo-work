#!/usr/bin/env python3
"""Run the persona safety scenarios and print a results table.

    .venv/bin/python scripts/run_evals.py

The scenarios are deterministic (no network, no model), so a pass means the safety rules held.
Exits non-zero if any scenario fails.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.personas import SCENARIOS  # noqa: E402


def main() -> int:
    width = max(len(name) for name, _, _ in SCENARIOS)
    passed = 0
    failures = []
    for name, description, scenario in SCENARIOS:
        try:
            asyncio.run(scenario())
            status = "PASS"
            passed += 1
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            status = "FAIL"
            failures.append((name, exc))
        print(f"{status}  {name:<{width}}  {description}")
    print(f"\n{passed}/{len(SCENARIOS)} scenarios passed the safety rubric")
    for name, exc in failures:
        print(f"\n--- {name}")
        traceback.print_exception(exc, limit=-3)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
