"""Google Search Console API authentication.

Legacy entry points kept for backwards compatibility (e.g. feeprobe imports
``gsc_mcp.auth.get_gsc_service``). Behaviour matches the original module:
failures print a diagnostic to stderr and raise ``SystemExit(1)``.
"""

from __future__ import annotations

import sys

from googleapiclient.discovery import build

from gsc_cli import core

DEFAULT_CREDENTIALS_PATH = core.DEFAULT_CREDENTIALS_PATH


def _load_credentials(scopes: list[str]):
    """Load credentials via the shared core, keeping legacy diagnostics."""
    creds_path = core.credentials_path()
    if not creds_path.is_file():
        print(
            f"Error: credentials file not found at {creds_path}\n"
            "Set GSC_CREDENTIALS_PATH or place a service-account JSON at "
            f"{DEFAULT_CREDENTIALS_PATH}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        return core.load_credentials(scopes)
    except core.GscCredentialsError as exc:
        print(f"Error: failed to load credentials — {exc.cause}", file=sys.stderr)
        raise SystemExit(1) from exc


def get_gsc_service():
    """Return an authenticated Google Search Console service object (readonly)."""
    credentials = _load_credentials(
        [core.WEBMASTERS_READONLY_SCOPE],
    )
    return build("searchconsole", "v1", credentials=credentials)


def get_indexing_service():
    """Return an authenticated Google Indexing API service object."""
    credentials = _load_credentials(
        [core.INDEXING_SCOPE],
    )
    return build("indexing", "v3", credentials=credentials)


def get_webmasters_service():
    """Return a Search Console service with read-write scope (URL Inspection)."""
    credentials = _load_credentials(
        [core.WEBMASTERS_SCOPE],
    )
    return build("searchconsole", "v1", credentials=credentials)
