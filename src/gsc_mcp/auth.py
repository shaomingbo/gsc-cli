"""Google Search Console API authentication."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

DEFAULT_CREDENTIALS_PATH = "~/.config/gsc-credentials.json"


def _load_credentials(scopes: list[str]) -> service_account.Credentials:
    """Load service-account credentials for the given *scopes*.

    Credentials path resolution:
      1. ``GSC_CREDENTIALS_PATH`` environment variable
      2. ``~/.config/gsc-credentials.json``

    Raises ``SystemExit`` with a human-readable message when credentials
    are missing or invalid — no raw stack traces are shown.
    """
    raw_path = os.environ.get("GSC_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)
    creds_path = Path(raw_path).expanduser()

    if not creds_path.is_file():
        print(
            f"Error: credentials file not found at {creds_path}\n"
            "Set GSC_CREDENTIALS_PATH or place a service-account JSON at "
            f"{DEFAULT_CREDENTIALS_PATH}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        return service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=scopes,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: failed to load credentials — {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def get_gsc_service():
    """Return an authenticated Google Search Console service object (readonly)."""
    credentials = _load_credentials(
        ["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    return build("searchconsole", "v1", credentials=credentials)


def get_indexing_service():
    """Return an authenticated Google Indexing API service object."""
    credentials = _load_credentials(
        ["https://www.googleapis.com/auth/indexing"],
    )
    return build("indexing", "v3", credentials=credentials)


def get_webmasters_service():
    """Return a Search Console service with read-write scope (URL Inspection)."""
    credentials = _load_credentials(
        ["https://www.googleapis.com/auth/webmasters"],
    )
    return build("searchconsole", "v1", credentials=credentials)
