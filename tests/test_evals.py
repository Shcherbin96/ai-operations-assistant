"""The golden eval suite is itself a regression gate — every scenario must pass."""

from evals.golden import run_scenario, scenarios
from evals.run import run


def test_every_golden_scenario_passes() -> None:
    for scenario in scenarios():
        assert run_scenario(scenario), scenario.name


def test_run_returns_zero_when_all_pass() -> None:
    assert run() == 0


def test_there_are_scenarios_for_each_core_guarantee() -> None:
    names = {s.name for s in scenarios()}
    assert {
        "read_only_auto_executes",
        "external_send_is_gated",
        "model_cannot_lower_risk",
        "unknown_tool_fails_closed",
        "destructive_is_disabled",
        "approval_is_single_use",
    } <= names
