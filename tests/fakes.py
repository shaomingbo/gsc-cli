"""Shared test fakes for gsc-cli tests (chainable googleapiclient stubs)."""

from __future__ import annotations

import httplib2
from googleapiclient.errors import HttpError

_REASONS = {
    401: "Unauthorized",
    403: "Forbidden",
    500: "Internal Server Error",
}


def make_http_error(status: int, payload: str) -> HttpError:
    resp = httplib2.Response({"status": str(status)})
    resp.reason = _REASONS.get(status, "Error")
    return HttpError(resp, payload.encode())


class FakeExecutor:
    """Chainable stand-in for googleapiclient resource objects.

    - ``response``: returned by execute() (single-call scenario).
    - ``error``: raised by execute() (single-call scenario).
    - ``per_call``: list of {"response": ...} / {"error": ...} consumed in
      order (multi-call scenario, used for submit_indexing batches).
    """

    def __init__(self, response=None, error=None, per_call=None):
        self._response = response or {}
        self._error = error
        self._per_call = per_call
        self._calls = 0

    def execute(self):
        if self._error is not None:
            raise self._error
        if self._per_call is not None:
            if self._calls >= len(self._per_call):
                raise AssertionError("execute() called more times than expected")
            call = self._per_call[self._calls]
            self._calls += 1
            if "error" in call:
                raise call["error"]
            return call.get("response", {})
        return self._response

    def __getattr__(self, name):
        def chain(*args, **kwargs):
            return self

        return chain
