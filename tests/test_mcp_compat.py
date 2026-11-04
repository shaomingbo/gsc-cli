"""Golden compatibility test: new MCP tools must reproduce base outputs verbatim.

Scenario data and expected outputs come from tests/golden_data.py, generated
by $EV/golden/generate_golden.py against the BASE (pre-refactor) code (V10).
"""

from __future__ import annotations

from unittest import mock

import pytest
from fakes import FakeExecutor, make_http_error
from golden_data import EXPECTED, SCENARIOS

import gsc_mcp.server as srv
from gsc_cli import core

TOOLS = {
    "performance": srv.gsc_performance,
    "sitemaps": srv.gsc_sitemaps,
    "inspect": srv.gsc_inspect_url,
    "submit": srv.gsc_submit_indexing,
}


def _service_for(scenario):
    if scenario.get("error"):
        err = scenario["error"]
        return FakeExecutor(error=make_http_error(err["status"], err["payload"]))
    if scenario.get("per_call") is not None:
        per_call = []
        for call in scenario["per_call"]:
            if "error" in call:
                per_call.append(
                    {
                        "error": make_http_error(
                            call["error"]["status"], call["error"]["payload"]
                        )
                    }
                )
            else:
                per_call.append({"response": call.get("response", {})})
        return FakeExecutor(per_call=per_call)
    return FakeExecutor(response=scenario.get("response", {}))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_tool_output_matches_golden(scenario, capsys):
    """Each scenario must reproduce the base-code output byte for byte."""
    tool = scenario["tool"]
    fn = TOOLS[tool]

    if scenario.get("auth") == "exit":
        patcher = mock.patch.object(
            core,
            "build_service",
            side_effect=core.GscCredentialsError(
                "credentials file not found at /nonexistent"
            ),
        )
    else:
        patcher = mock.patch.object(
            core, "build_service", return_value=_service_for(scenario)
        )

    with patcher:
        output = fn(**scenario["kwargs"])

    assert output == EXPECTED[scenario["id"]], f"golden mismatch for {scenario['id']!r}"


def test_legacy_auth_entry_points_importable():
    """The four legacy gsc_mcp.auth names must keep existing (V9)."""
    from gsc_mcp.auth import (  # noqa: F401
        DEFAULT_CREDENTIALS_PATH,
        get_gsc_service,
        get_indexing_service,
        get_webmasters_service,
    )
