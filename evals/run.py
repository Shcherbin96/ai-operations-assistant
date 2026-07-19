"""Run the offline golden scenarios; exit non-zero if any fail (regression gate).

uv run python -m evals.run
"""

from __future__ import annotations

from evals.golden import run_scenario, scenarios


def run() -> int:
    results = [(scenario.name, run_scenario(scenario)) for scenario in scenarios()]
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} golden scenarios passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
