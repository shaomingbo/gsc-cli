"""Core Google Search Console operations shared by the ``gsc`` CLI and ``gsc-mcp``.

This module only fetches data and classifies errors; it never formats text.
Functions return the raw Google API response as a dict. Failures raise one of
the ``Gsc*Error`` exceptions below (mapping: usage -> exit 2, credentials ->
exit 3, permission -> exit 4, api -> exit 5 in the CLI).

``submit_indexing`` is the documented special case: it returns per-URL results
instead of a single response dict (see its docstring).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

DEFAULT_CREDENTIALS_PATH = "~/.config/gsc-credentials.json"

WEBMASTERS_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WEBMASTERS_SCOPE = "https://www.googleapis.com/auth/webmasters"
INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_DIMENSIONS = {"query", "page", "date", "country", "device"}
_VALID_ACTIONS = ("URL_UPDATED", "URL_DELETED")


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class GscError(Exception):
    """Base class for all gsc errors."""


class GscUsageError(GscError):
    """Invalid arguments (parameter validation failure)."""


class GscCredentialsError(GscError):
    """Credentials file missing or failed to parse.

    ``cause`` keeps the original parsing exception (None when the file is
    missing) so the legacy ``gsc_mcp.auth`` shim can reproduce its exact
    historical diagnostics.
    """

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class GscHttpError(GscError):
    """HTTP/network failure; ``str()`` reproduces the original error text."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause

    def __str__(self) -> str:
        return str(self.cause)


class GscPermissionError(GscHttpError):
    """Google returned HTTP 401 or 403 (insufficient permission)."""


class GscApiError(GscHttpError):
    """Any other HTTP error or network failure."""


def _classify_http_error(exc: Exception) -> GscHttpError:
    if isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (401, 403, "401", "403"):
            return GscPermissionError(exc)
    return GscApiError(exc)


# ---------------------------------------------------------------------------
# Credentials and service construction
# ---------------------------------------------------------------------------


def credentials_path() -> Path:
    """Resolve the credentials path (``GSC_CREDENTIALS_PATH`` wins)."""
    raw = os.environ.get("GSC_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)
    return Path(raw).expanduser()


def load_credentials(scopes: list[str]) -> service_account.Credentials:
    """Load service-account credentials for *scopes* (raises GscCredentialsError)."""
    creds_path = credentials_path()
    if not creds_path.is_file():
        raise GscCredentialsError(f"credentials file not found at {creds_path}")
    try:
        return service_account.Credentials.from_service_account_file(
            str(creds_path), scopes=scopes
        )
    except Exception as exc:
        raise GscCredentialsError(
            f"failed to load credentials — {exc}", cause=exc
        ) from exc


def build_service(name: str, version: str, scopes: list[str]):
    """Build an authenticated googleapiclient service object."""
    credentials = load_credentials(scopes)
    return build(name, version, credentials=credentials)


# ---------------------------------------------------------------------------
# Argument validation (rules identical to the legacy MCP tools)
# ---------------------------------------------------------------------------


def normalise_site(site: str) -> str:
    """Ensure *site* uses the format the API expects (``sc-domain:`` prefix)."""
    if site.startswith(("sc-domain:", "https://", "http://")):
        return site
    return f"sc-domain:{site}"


def _parse_dimensions(dimensions: str) -> list[str]:
    dim_list = [d.strip() for d in dimensions.split(",")]
    invalid = [d for d in dim_list if d not in _VALID_DIMENSIONS]
    if invalid:
        raise GscUsageError(
            f"invalid dimension(s): {', '.join(invalid)}. "
            f"Allowed values: {', '.join(sorted(_VALID_DIMENSIONS))}."
        )
    return dim_list


def _validate_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise GscUsageError(f"url must start with http:// or https://, got '{url}'")


def _validate_action(action: str) -> None:
    if action not in _VALID_ACTIONS:
        raise GscUsageError(
            f"action must be URL_UPDATED or URL_DELETED, got '{action}'"
        )


# ---------------------------------------------------------------------------
# Read operations (webmasters.readonly scope only)
# ---------------------------------------------------------------------------


def list_sites() -> dict:
    """List all sites the credential has access to."""
    try:
        service = build_service("searchconsole", "v1", [WEBMASTERS_READONLY_SCOPE])
        return service.sites().list().execute()
    except GscError:
        raise
    except Exception as exc:
        raise _classify_http_error(exc) from exc


def query_performance(
    site: str,
    start_date: str,
    end_date: str,
    dimensions: str = "query",
    row_limit: int = 25,
    page_filter: str | None = None,
    query_filter: str | None = None,
) -> dict:
    """Query search-analytics performance data; returns the raw response."""
    if not _DATE_RE.match(start_date):
        raise GscUsageError(
            f"start_date '{start_date}' is not valid. "
            "Expected format: YYYY-MM-DD (e.g. 2026-01-15)."
        )
    if not _DATE_RE.match(end_date):
        raise GscUsageError(
            f"end_date '{end_date}' is not valid. "
            "Expected format: YYYY-MM-DD (e.g. 2026-01-15)."
        )
    dim_list = _parse_dimensions(dimensions)
    if not (1 <= row_limit <= 25000):
        raise GscUsageError(f"row_limit must be between 1 and 25000, got {row_limit}.")

    site_url = normalise_site(site)

    body: dict = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dim_list,
        "rowLimit": row_limit,
    }
    filters = []
    if page_filter:
        filters.append(
            {"dimension": "page", "operator": "contains", "expression": page_filter}
        )
    if query_filter:
        filters.append(
            {"dimension": "query", "operator": "contains", "expression": query_filter}
        )
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    try:
        service = build_service("searchconsole", "v1", [WEBMASTERS_READONLY_SCOPE])
        return service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    except GscError:
        raise
    except Exception as exc:
        raise _classify_http_error(exc) from exc


def list_sitemaps(site: str) -> dict:
    """List sitemaps registered for *site*; returns the raw response."""
    site_url = normalise_site(site)
    try:
        service = build_service("searchconsole", "v1", [WEBMASTERS_READONLY_SCOPE])
        return service.sitemaps().list(siteUrl=site_url).execute()
    except GscError:
        raise
    except Exception as exc:
        raise _classify_http_error(exc) from exc


def inspect_url(site: str, url: str) -> dict:
    """Inspect *url* via the URL Inspection API (readonly scope); raw response."""
    _validate_url(url)
    site_url = normalise_site(site)
    body = {
        "inspectionUrl": url,
        "siteUrl": site_url,
    }
    try:
        service = build_service("searchconsole", "v1", [WEBMASTERS_READONLY_SCOPE])
        return service.urlInspection().index().inspect(body=body).execute()
    except GscError:
        raise
    except Exception as exc:
        raise _classify_http_error(exc) from exc


# ---------------------------------------------------------------------------
# Write operation (indexing scope)
# ---------------------------------------------------------------------------


def submit_indexing(urls: list[str], action: str = "URL_UPDATED") -> dict:
    """Submit URLs to the Google Indexing API.

    Special-case contract (plan 2.3): parameter validation failures and
    credential errors are raised (GscUsageError / GscCredentialsError) before
    any URL is submitted. Once submitting, a single URL's failure never
    aborts the remaining URLs.

    Returns ``{"action": str, "results": [...]}`` where each result keeps:
    - success: the raw publish response dict (``response``), the parsed
      ``notify_time`` (from urlNotificationMetadata, "unknown" when absent);
    - failure: the original exception text (``error``) and its classification
      (``error_type``: "permission" for HTTP 401/403, "api" otherwise).
    """
    _validate_action(action)
    url_list = [u.strip() for u in urls if u.strip()]
    if not url_list:
        raise GscUsageError("no URLs provided")
    invalid_urls = [u for u in url_list if not u.startswith(("http://", "https://"))]
    if invalid_urls:
        raise GscUsageError(
            f"each URL must start with http:// or https://. "
            f"Invalid: {', '.join(invalid_urls)}"
        )

    try:
        service = build_service("indexing", "v3", [INDEXING_SCOPE])
    except GscError:
        raise
    except Exception as exc:
        raise _classify_http_error(exc) from exc

    results: list[dict] = []
    for url in url_list:
        body = {"url": url, "type": action}
        # Same per-URL exception boundary as the legacy server.py: parsing the
        # publish response stays inside the try so a malformed response for
        # one URL never aborts the remaining URLs (FINAL-01).
        try:
            response = service.urlNotifications().publish(body=body).execute()
            notif = response.get("urlNotificationMetadata", {})
            latest = notif.get("latestUpdate", {}) or notif.get("latestRemove", {})
            notify_time = latest.get("notifyTime", "unknown")
        except Exception as exc:  # noqa: BLE001 — per-URL failures must not abort
            classified = _classify_http_error(exc)
            results.append(
                {
                    "url": url,
                    "ok": False,
                    "error": str(exc),
                    "error_type": (
                        "permission"
                        if isinstance(classified, GscPermissionError)
                        else "api"
                    ),
                }
            )
            continue
        results.append(
            {"url": url, "ok": True, "notify_time": notify_time, "response": response}
        )
    return {"action": action, "results": results}
