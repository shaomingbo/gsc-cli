"""Tests for gsc_cli.core: validation, scopes, error classification, batches."""

from __future__ import annotations

from unittest import mock

import pytest
from fakes import FakeExecutor, make_http_error

from gsc_cli import core

READONLY = [core.WEBMASTERS_READONLY_SCOPE]
INDEXING = [core.INDEXING_SCOPE]

PERF_OK = {
    "rows": [
        {"keys": ["k"], "clicks": 1, "impressions": 2, "ctr": 0.5, "position": 1.5}
    ]
}
E403 = {"kind": "http_error", "status": 403, "payload": '{"error": {"code": 403}}'}


def _http(status, payload):
    return make_http_error(status, payload)


# ---------------------------------------------------------------------------
# Argument validation (rules identical to the legacy MCP tools)
# ---------------------------------------------------------------------------


def test_normalise_site():
    assert core.normalise_site("feeprobe.com") == "sc-domain:feeprobe.com"
    assert core.normalise_site("sc-domain:feeprobe.com") == "sc-domain:feeprobe.com"
    assert core.normalise_site("https://feeprobe.com") == "https://feeprobe.com"
    assert core.normalise_site("http://feeprobe.com") == "http://feeprobe.com"


@pytest.mark.parametrize("bad", ["2026/10/01", "10-01-2026", "2026-1-1", "x"])
def test_bad_start_date(bad):
    with pytest.raises(core.GscUsageError):
        core.query_performance("s.com", bad, "2026-10-07")


def test_bad_end_date():
    with pytest.raises(core.GscUsageError, match="end_date"):
        core.query_performance("s.com", "2026-10-01", "nope")


def test_bad_dimensions():
    with pytest.raises(core.GscUsageError, match="invalid dimension"):
        core.query_performance(
            "s.com", "2026-10-01", "2026-10-07", dimensions="query,foo"
        )


@pytest.mark.parametrize("limit", [0, -1, 25001])
def test_bad_row_limit(limit):
    with pytest.raises(core.GscUsageError, match="row_limit"):
        core.query_performance("s.com", "2026-10-01", "2026-10-07", row_limit=limit)


def test_inspect_rejects_non_http_url():
    with pytest.raises(core.GscUsageError, match="http://"):
        core.inspect_url("s.com", "ftp://s.com/")


def test_submit_rejects_bad_action():
    with pytest.raises(core.GscUsageError, match="URL_UPDATED or URL_DELETED"):
        core.submit_indexing(["https://s.com/"], action="FOO")


def test_submit_rejects_empty_urls():
    with pytest.raises(core.GscUsageError, match="no URLs"):
        core.submit_indexing(["  "])


def test_submit_rejects_non_http_urls():
    with pytest.raises(core.GscUsageError, match="must start with http"):
        core.submit_indexing(["https://s.com/", "ftp://s.com/x"])


# ---------------------------------------------------------------------------
# Scope selection (queries stay read-only; only submit asks for indexing)
# ---------------------------------------------------------------------------


def _scope_recorder(service):
    calls = []

    def recorder(name, version, scopes):
        calls.append((name, version, list(scopes)))
        return service

    return recorder, calls


def test_scope_selection_all_tools():
    for run in (
        lambda: core.list_sites(),
        lambda: core.query_performance("s.com", "2026-10-01", "2026-10-07"),
        lambda: core.list_sitemaps("s.com"),
        lambda: core.inspect_url("s.com", "https://s.com/"),
    ):
        recorder, calls = _scope_recorder(FakeExecutor(response={}))
        with mock.patch.object(core, "build_service", side_effect=recorder):
            run()
        assert calls == [("searchconsole", "v1", READONLY)], calls

    recorder, calls = _scope_recorder(
        FakeExecutor(
            per_call=[
                {
                    "response": {
                        "urlNotificationMetadata": {"latestUpdate": {"notifyTime": "t"}}
                    }
                }
            ]
        )
    )
    with mock.patch.object(core, "build_service", side_effect=recorder):
        core.submit_indexing(["https://s.com/"])
    assert calls == [("indexing", "v3", INDEXING)], calls


# ---------------------------------------------------------------------------
# Error classification (401/403 -> permission, other HTTP -> api)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_http_401_403_is_permission(status):
    err = _http(status, f'{{"error": {{"code": {status}, "message": "no"}}}}')
    with (
        mock.patch.object(core, "build_service", return_value=FakeExecutor(error=err)),
        pytest.raises(core.GscPermissionError) as excinfo,
    ):
        core.query_performance("s.com", "2026-10-01", "2026-10-07")
    assert str(excinfo.value) == str(err)


def test_http_500_is_api_error():
    err = _http(500, '{"error": {"code": 500}}')
    with (
        mock.patch.object(core, "build_service", return_value=FakeExecutor(error=err)),
        pytest.raises(core.GscApiError) as excinfo,
    ):
        core.list_sitemaps("s.com")
    assert str(excinfo.value) == str(err)


def test_network_error_is_api_error():
    with (
        mock.patch.object(
            core, "build_service", return_value=FakeExecutor(error=OSError("boom"))
        ),
        pytest.raises(core.GscApiError),
    ):
        core.list_sites()


def test_missing_credentials(monkeypatch):
    monkeypatch.setenv("GSC_CREDENTIALS_PATH", "/nonexistent-gsc-creds.json")
    with pytest.raises(core.GscCredentialsError):
        core.list_sites()
    with pytest.raises(core.GscCredentialsError):
        core.submit_indexing(["https://s.com/"])


# ---------------------------------------------------------------------------
# submit_indexing batch contract (special case)
# ---------------------------------------------------------------------------


def _ok_response(time):
    return {"urlNotificationMetadata": {"latestUpdate": {"notifyTime": time}}}


def test_submit_mixed_batch_one_failure_then_success():
    err = _http(403, '{"error": {"code": 403, "message": "Permission denied."}}')
    service = FakeExecutor(
        per_call=[
            {"error": err},
            {"response": _ok_response("2026-11-03T09:02:03Z")},
        ]
    )
    with mock.patch.object(core, "build_service", return_value=service):
        result = core.submit_indexing(["https://s.com/missing", "https://s.com/"])

    assert result["action"] == "URL_UPDATED"
    fail, ok = result["results"]
    assert fail["url"] == "https://s.com/missing"
    assert fail["ok"] is False
    assert fail["error"] == str(err)  # original exception text preserved
    assert fail["error_type"] == "permission"
    assert ok["ok"] is True
    assert ok["notify_time"] == "2026-11-03T09:02:03Z"
    assert ok["response"] == _ok_response("2026-11-03T09:02:03Z")  # raw response


def test_submit_all_ok_keeps_raw_responses():
    service = FakeExecutor(
        per_call=[
            {"response": _ok_response("t1")},
            {
                "response": {
                    "urlNotificationMetadata": {"latestRemove": {"notifyTime": "t2"}}
                }
            },
        ]
    )
    with mock.patch.object(core, "build_service", return_value=service):
        result = core.submit_indexing(
            ["https://s.com/a", "https://s.com/b"], action="URL_DELETED"
        )
    assert result["action"] == "URL_DELETED"
    assert [r["notify_time"] for r in result["results"]] == ["t1", "t2"]
    assert all(r["ok"] for r in result["results"])


def test_submit_unknown_notify_time():
    service = FakeExecutor(per_call=[{"response": {"urlNotificationMetadata": {}}}])
    with mock.patch.object(core, "build_service", return_value=service):
        result = core.submit_indexing(["https://s.com/"])
    assert result["results"][0]["notify_time"] == "unknown"


def test_submit_api_failure_classified_as_api():
    err = _http(500, '{"error": {"code": 500}}')
    service = FakeExecutor(per_call=[{"error": err}])
    with mock.patch.object(core, "build_service", return_value=service):
        result = core.submit_indexing(["https://s.com/"])
    assert result["results"][0]["error_type"] == "api"


def test_submit_malformed_metadata_does_not_abort_batch():
    """FINAL-01: parsing failures keep the legacy per-URL exception boundary.

    First publish returns {"urlNotificationMetadata": null} (base code raised
    AttributeError inside its per-URL try); the second URL must still submit.
    """
    service = FakeExecutor(
        per_call=[
            {"response": {"urlNotificationMetadata": None}},
            {
                "response": {
                    "urlNotificationMetadata": {
                        "latestUpdate": {"notifyTime": "2026-11-04T01:00:00Z"}
                    }
                }
            },
        ]
    )
    with mock.patch.object(core, "build_service", return_value=service):
        result = core.submit_indexing(["https://s.com/a", "https://s.com/b"])

    fail, ok = result["results"]
    assert fail["url"] == "https://s.com/a"
    assert fail["ok"] is False
    assert "'NoneType' object has no attribute 'get'" in fail["error"]
    assert fail["error_type"] == "api"
    assert ok == {
        "url": "https://s.com/b",
        "ok": True,
        "notify_time": "2026-11-04T01:00:00Z",
        "response": {
            "urlNotificationMetadata": {
                "latestUpdate": {"notifyTime": "2026-11-04T01:00:00Z"}
            }
        },
    }


def test_submit_credentials_error_before_any_call():
    def boom(name, version, scopes):
        raise core.GscCredentialsError("credentials file not found at /x")

    with (
        mock.patch.object(core, "build_service", side_effect=boom),
        pytest.raises(core.GscCredentialsError),
    ):
        core.submit_indexing(["https://s.com/"])


# ---------------------------------------------------------------------------
# Query responses pass through raw
# ---------------------------------------------------------------------------


def test_query_performance_returns_raw_response():
    with mock.patch.object(
        core, "build_service", return_value=FakeExecutor(response=PERF_OK)
    ):
        assert core.query_performance("s.com", "2026-10-01", "2026-10-07") == PERF_OK
