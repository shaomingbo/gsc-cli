"""Tests for gsc_cli.cli: JSON shapes, exit codes, --write gate, precision."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fakes import make_http_error

from gsc_cli import cli, core

PERF_RAW = {
    "rows": [
        {
            "keys": ["notion review"],
            "clicks": 120,
            "impressions": 3400,
            "ctr": 0.03529411764705882,
            "position": 12.3456789,
        },
        {
            "keys": ["clickup"],
            "clicks": 7,
            "impressions": 210,
            "ctr": 0.03333333333333333,
            "position": 3.5,
        },
    ]
}

SITES_RAW = {
    "siteEntry": [
        {"siteUrl": "sc-domain:feeprobe.com", "permissionLevel": "siteFullUser"},
        {"siteUrl": "sc-domain:saasprobe.com", "permissionLevel": "siteFullUser"},
    ]
}

SITEMAPS_RAW = {
    "sitemap": [
        {
            "path": "https://feeprobe.com/sitemap.xml",
            "isPending": False,
            "warnings": 2,
            "errors": 0,
        },
    ]
}

INSPECT_RAW = {"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}}

SUBMIT_MIXED = {
    "action": "URL_UPDATED",
    "results": [
        {
            "url": "https://s.com/missing",
            "ok": False,
            "error": "<HttpError 403 ...>",
            "error_type": "permission",
        },
        {
            "url": "https://s.com/",
            "ok": True,
            "notify_time": "t1",
            "response": {
                "urlNotificationMetadata": {"latestUpdate": {"notifyTime": "t1"}}
            },
        },
    ],
}


def _run_cli(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _error_json(stderr):
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected a single stderr line, got: {stderr!r}"
    return json.loads(lines[0])["error"]


# ---------------------------------------------------------------------------
# Success: JSON shapes for every subcommand
# ---------------------------------------------------------------------------


def test_sites_json_shape(capsys):
    with mock.patch.object(core, "list_sites", return_value=SITES_RAW):
        code, out, err = _run_cli(["sites"], capsys)
    assert code == 0 and err == ""
    payload = json.loads(out)
    assert payload == {"sites": SITES_RAW["siteEntry"]}


def test_performance_json_shape_and_precision(capsys):
    with mock.patch.object(core, "query_performance", return_value=PERF_RAW):
        code, out, _ = _run_cli(
            [
                "performance",
                "feeprobe.com",
                "--start",
                "2026-10-01",
                "--end",
                "2026-10-07",
                "--limit",
                "10",
            ],
            capsys,
        )
    assert code == 0
    payload = json.loads(out)
    assert payload["site"] == "sc-domain:feeprobe.com"
    assert payload["start_date"] == "2026-10-01"
    assert payload["end_date"] == "2026-10-07"
    assert payload["dimensions"] == ["query"]
    row = payload["rows"][0]
    assert row["query"] == "notion review"
    # Raw values survive without rounding (bit-for-bit float round-trip).
    assert row["clicks"] == 120
    assert row["impressions"] == 3400
    assert row["ctr"] == 0.03529411764705882
    assert row["position"] == 12.3456789
    assert payload["rows"][1]["ctr"] == 0.03333333333333333


def test_sitemaps_json_shape(capsys):
    with mock.patch.object(core, "list_sitemaps", return_value=SITEMAPS_RAW):
        _code, out, _ = _run_cli(["sitemaps", "feeprobe.com"], capsys)
    payload = json.loads(out)
    assert payload == {
        "site": "sc-domain:feeprobe.com",
        "sitemaps": SITEMAPS_RAW["sitemap"],
    }


def test_inspect_url_json_shape(capsys):
    with mock.patch.object(core, "inspect_url", return_value=INSPECT_RAW):
        _code, out, _ = _run_cli(
            ["inspect-url", "feeprobe.com", "https://feeprobe.com/"], capsys
        )
    payload = json.loads(out)
    assert payload == {
        "site": "sc-domain:feeprobe.com",
        "url": "https://feeprobe.com/",
        "inspection": INSPECT_RAW["inspectionResult"],
    }


def test_submit_indexing_json_shape(capsys):
    with mock.patch.object(core, "submit_indexing", return_value=SUBMIT_MIXED):
        _code, out, _ = _run_cli(
            ["submit-indexing", "--write", "https://s.com/missing", "https://s.com/"],
            capsys,
        )
    payload = json.loads(out)
    assert payload["action"] == "URL_UPDATED"
    assert payload["results"][0] == {
        "url": "https://s.com/missing",
        "ok": False,
        "error": "<HttpError 403 ...>",
    }
    assert payload["results"][1] == {
        "url": "https://s.com/",
        "ok": True,
        "notify_time": "t1",
    }


def test_table_format(capsys):
    with mock.patch.object(core, "query_performance", return_value=PERF_RAW):
        code, out, _ = _run_cli(
            [
                "--format",
                "table",
                "performance",
                "feeprobe.com",
                "--start",
                "2026-10-01",
                "--end",
                "2026-10-07",
            ],
            capsys,
        )
    assert code == 0
    assert "query" in out and "clicks" in out and "notion review" in out


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "0.2.0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Failure: one-line JSON errors on stderr, mapped exit codes, no traceback
# ---------------------------------------------------------------------------


def test_exit_2_without_write_and_without_credentials(capsys, monkeypatch):
    # V3: refuse --write-less submit BEFORE reading credentials (the bogus
    # credentials path would exit 3 if the CLI tried to load them).
    monkeypatch.setenv("GSC_CREDENTIALS_PATH", "/nonexistent-gsc-creds.json")
    with mock.patch.object(core, "submit_indexing") as never:
        code, _out, err = _run_cli(["submit-indexing", "https://feeprobe.com/"], capsys)
    never.assert_not_called()
    assert code == 2
    error = _error_json(err)
    assert error["type"] == "usage"
    assert "--write" in error["message"]
    assert "Traceback" not in err


def test_exit_2_argparse_error_is_json(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["performance", "feeprobe.com", "--end", "2026-10-07"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    error = _error_json(err)
    assert error["type"] == "usage"
    assert "Traceback" not in err


def test_exit_3_missing_credentials(capsys, monkeypatch):
    monkeypatch.setenv("GSC_CREDENTIALS_PATH", "/nonexistent-gsc-creds.json")
    code, _out, err = _run_cli(["sites"], capsys)
    assert code == 3
    error = _error_json(err)
    assert error["type"] == "credentials"
    assert "Traceback" not in err


def test_exit_4_permission(capsys):
    err403 = make_http_error(403, '{"error": {"code": 403}}')
    with mock.patch.object(
        core, "query_performance", side_effect=core.GscPermissionError(err403)
    ):
        code, _out, err = _run_cli(
            [
                "performance",
                "feeprobe.com",
                "--start",
                "2026-10-01",
                "--end",
                "2026-10-07",
            ],
            capsys,
        )
    assert code == 4
    error = _error_json(err)
    assert error["type"] == "permission"
    # Must convey: read-only accounts cannot submit; submitting needs an owner.
    lowered = error["message"].lower()
    assert "read-only" in lowered and "owner" in lowered
    assert "Traceback" not in err


def test_exit_5_api_error(capsys):
    err500 = make_http_error(500, '{"error": {"code": 500}}')
    with mock.patch.object(
        core, "query_performance", side_effect=core.GscApiError(err500)
    ):
        code, _out, err = _run_cli(
            [
                "performance",
                "feeprobe.com",
                "--start",
                "2026-10-01",
                "--end",
                "2026-10-07",
            ],
            capsys,
        )
    assert code == 5
    assert _error_json(err)["type"] == "api"
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# submit-indexing batch exit-code precedence (plan 2.3)
# ---------------------------------------------------------------------------


def test_submit_mixed_permission_failure_wins_over_success(capsys):
    with mock.patch.object(core, "submit_indexing", return_value=SUBMIT_MIXED):
        code, out, err = _run_cli(
            ["submit-indexing", "--write", "https://s.com/missing", "https://s.com/"],
            capsys,
        )
    assert code == 4  # permission failure beats the successful URL
    assert json.loads(out)["results"][1]["ok"] is True  # stdout keeps results
    assert _error_json(err)["type"] == "permission"
    assert "Traceback" not in err


def test_submit_mixed_api_failure_exits_5(capsys):
    mixed = {
        "action": "URL_UPDATED",
        "results": [
            {
                "url": "https://s.com/x",
                "ok": False,
                "error": "boom",
                "error_type": "api",
            },
            {"url": "https://s.com/", "ok": True, "notify_time": "t"},
        ],
    }
    with mock.patch.object(core, "submit_indexing", return_value=mixed):
        code, _out, err = _run_cli(
            ["submit-indexing", "--write", "https://s.com/x", "https://s.com/"], capsys
        )
    assert code == 5
    assert _error_json(err)["type"] == "api"


def test_submit_all_ok_exits_0(capsys):
    allok = {
        "action": "URL_UPDATED",
        "results": [
            {"url": "https://s.com/", "ok": True, "notify_time": "t"},
        ],
    }
    with mock.patch.object(core, "submit_indexing", return_value=allok):
        code, _out, _ = _run_cli(
            ["submit-indexing", "--write", "https://s.com/"], capsys
        )
    assert code == 0


def test_exit_2_abbreviated_write_is_rejected(capsys, monkeypatch):
    """FINAL-02: --w/--wri must NOT satisfy the --write gate."""
    monkeypatch.setenv("GSC_CREDENTIALS_PATH", "/nonexistent-gsc-creds.json")
    for argv in (
        ["submit-indexing", "--w", "https://feeprobe.com/"],
        ["submit-indexing", "--wri", "https://feeprobe.com/"],
    ):
        with (
            mock.patch.object(core, "submit_indexing") as never,
            pytest.raises(SystemExit) as excinfo,
        ):
            cli.main(argv)
        never.assert_not_called()
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        error = _error_json(err)
        assert error["type"] == "usage"
        assert "Traceback" not in err


def test_submit_malformed_metadata_batch_exits_5_without_traceback(capsys):
    """FINAL-01: parsing failure in one URL keeps results complete, exit 5."""
    mixed = {
        "action": "URL_UPDATED",
        "results": [
            {
                "url": "https://s.com/a",
                "ok": False,
                "error": "'NoneType' object has no attribute 'get'",
                "error_type": "api",
            },
            {"url": "https://s.com/b", "ok": True, "notify_time": "t"},
        ],
    }
    with mock.patch.object(core, "submit_indexing", return_value=mixed):
        code, out, err = _run_cli(
            ["submit-indexing", "--write", "https://s.com/a", "https://s.com/b"], capsys
        )
    assert code == 5
    payload = json.loads(out)
    assert payload["results"][1]["ok"] is True
    assert "Traceback" not in err
