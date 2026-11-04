"""``gsc`` — command-line interface for Google Search Console.

Query commands are read-only and print JSON by default. The write operation
(``submit-indexing``) refuses to run without an explicit ``--write``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from gsc_cli import __version__, core

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CREDENTIALS = 3
EXIT_PERMISSION = 4
EXIT_API = 5


class _JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose errors are a single JSON line on stderr (exit 2).

    Abbreviations are disabled (allow_abbrev=False) so e.g. ``--w`` can never
    satisfy the ``--write`` gate (FINAL-02).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        emit_error("usage", message)
        raise SystemExit(EXIT_USAGE)


def emit_error(error_type: str, message: str) -> None:
    print(
        json.dumps({"error": {"type": error_type, "message": message}}),
        file=sys.stderr,
    )


def build_parser() -> _JSONArgumentParser:
    parser = _JSONArgumentParser(
        prog="gsc", description="Google Search Console command-line tool."
    )
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(
        dest="command", required=True, parser_class=_JSONArgumentParser
    )

    sub.add_parser("sites", help="List sites the credential can access.")

    perf = sub.add_parser("performance", help="Query search-analytics data.")
    perf.add_argument("site")
    perf.add_argument("--start", required=True)
    perf.add_argument("--end", required=True)
    perf.add_argument("--dimensions", default="query")
    perf.add_argument("--limit", type=int, default=25)
    perf.add_argument("--page-contains", default=None)
    perf.add_argument("--query-contains", default=None)

    smap = sub.add_parser("sitemaps", help="List sitemaps registered for a site.")
    smap.add_argument("site")

    insp = sub.add_parser("inspect-url", help="Inspect a URL (read-only).")
    insp.add_argument("site")
    insp.add_argument("url")

    subm = sub.add_parser(
        "submit-indexing", help="Submit URLs to the Indexing API (write)."
    )
    subm.add_argument("urls", nargs="+")
    subm.add_argument(
        "--action", choices=["URL_UPDATED", "URL_DELETED"], default="URL_UPDATED"
    )
    subm.add_argument("--write", action="store_true")

    return parser


# ---------------------------------------------------------------------------
# Payload assembly (raw values, no rounding)
# ---------------------------------------------------------------------------


def _performance_payload(
    site_url: str,
    start_date: str,
    end_date: str,
    dim_list: list[str],
    response: dict,
) -> dict:
    rows = []
    for row in response.get("rows", []):
        item: dict[str, Any] = {}
        for i, dim in enumerate(dim_list):
            keys = row.get("keys", [])
            item[dim] = keys[i] if i < len(keys) else None
        item["clicks"] = row.get("clicks", 0)
        item["impressions"] = row.get("impressions", 0)
        item["ctr"] = row.get("ctr", 0.0)
        item["position"] = row.get("position", 0.0)
        rows.append(item)
    return {
        "site": site_url,
        "start_date": start_date,
        "end_date": end_date,
        "dimensions": dim_list,
        "rows": rows,
    }


def _submit_payload(result: dict) -> dict:
    """Internal payload: failure items keep ``_error_type`` for exit-code
    precedence; it is stripped before printing."""
    items = []
    for item in result["results"]:
        if item["ok"]:
            items.append(
                {"url": item["url"], "ok": True, "notify_time": item["notify_time"]}
            )
        else:
            items.append(
                {
                    "url": item["url"],
                    "ok": False,
                    "error": item["error"],
                    "_error_type": item["error_type"],
                }
            )
    return {"action": result["action"], "results": items}


def _strip_error_types(payload: dict) -> dict:
    return {
        "action": payload["action"],
        "results": [
            {k: v for k, v in r.items() if k != "_error_type"}
            for r in payload["results"]
        ],
    }


def run(args: argparse.Namespace) -> dict:
    """Execute the parsed command and return the JSON payload (raw values)."""
    if args.command == "submit-indexing" and not args.write:
        # Refuse before touching credentials or the network (V3).
        raise core.GscUsageError(
            "submit-indexing is a write operation; pass --write to execute it"
        )

    if args.command == "sites":
        return {"sites": core.list_sites().get("siteEntry", [])}

    if args.command == "performance":
        site_url = core.normalise_site(args.site)
        dim_list = [d.strip() for d in args.dimensions.split(",")]
        response = core.query_performance(
            args.site,
            args.start,
            args.end,
            dimensions=args.dimensions,
            row_limit=args.limit,
            page_filter=args.page_contains,
            query_filter=args.query_contains,
        )
        return _performance_payload(site_url, args.start, args.end, dim_list, response)

    if args.command == "sitemaps":
        site_url = core.normalise_site(args.site)
        return {
            "site": site_url,
            "sitemaps": core.list_sitemaps(args.site).get("sitemap", []),
        }

    if args.command == "inspect-url":
        site_url = core.normalise_site(args.site)
        response = core.inspect_url(args.site, args.url)
        return {
            "site": site_url,
            "url": args.url,
            "inspection": response.get("inspectionResult", {}),
        }

    if args.command == "submit-indexing":
        return _submit_payload(core.submit_indexing(args.urls, action=args.action))

    raise core.GscUsageError(f"unknown command: {args.command}")


# ---------------------------------------------------------------------------
# Table rendering (aligned plain text, no extra dependencies)
# ---------------------------------------------------------------------------


def _render_table(command: str, payload: dict) -> str:
    if command == "performance":
        headers = payload["dimensions"] + [
            "clicks",
            "impressions",
            "ctr",
            "position",
        ]
        rows = [
            [str(row[h]) for h in payload["dimensions"]]
            + [
                str(row["clicks"]),
                str(row["impressions"]),
                str(row["ctr"]),
                str(row["position"]),
            ]
            for row in payload["rows"]
        ]
        return _align(headers, rows)

    if command == "sites":
        headers = ["siteUrl", "permissionLevel"]
        rows = [
            [s.get("siteUrl", ""), s.get("permissionLevel", "")]
            for s in payload["sites"]
        ]
        return _align(headers, rows)

    if command == "sitemaps":
        headers = [
            "path",
            "type",
            "lastSubmitted",
            "lastDownloaded",
            "isPending",
            "warnings",
            "errors",
        ]
        rows = [
            [
                str(sm.get("path", "N/A")),
                str(sm.get("type", "N/A")),
                str(sm.get("lastSubmitted", "N/A")),
                str(sm.get("lastDownloaded", "N/A")),
                str(sm.get("isPending", False)),
                str(sm.get("warnings", "0")),
                str(sm.get("errors", "0")),
            ]
            for sm in payload["sitemaps"]
        ]
        return _align(headers, rows)

    if command == "inspect-url":
        headers = ["field", "value"]
        rows = [[k, str(v)] for k, v in payload["inspection"].items()]
        return _align(headers, rows)

    if command == "submit-indexing":
        headers = ["url", "status", "detail"]
        rows = [
            [r["url"], "OK", r["notify_time"]]
            if r["ok"]
            else [r["url"], "FAIL", r["error"]]
            for r in payload["results"]
        ]
        return _align(headers, rows)

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _align(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip(),
        "  ".join("-" * w for w in widths),
    ]
    for row in rows:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _submit_exit_code(payload: dict) -> int:
    """Exit-code precedence for submit-indexing batches (plan 2.3)."""
    error_types = {r.get("_error_type") for r in payload["results"] if not r.get("ok")}
    if "permission" in error_types:
        return EXIT_PERMISSION
    if error_types:
        return EXIT_API
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        payload = run(args)
    except core.GscUsageError as exc:
        emit_error("usage", str(exc))
        return EXIT_USAGE
    except core.GscCredentialsError as exc:
        emit_error("credentials", f"credentials error: {exc}")
        return EXIT_CREDENTIALS
    except core.GscPermissionError as exc:
        emit_error("permission", _permission_message(exc))
        return EXIT_PERMISSION
    except core.GscApiError as exc:
        emit_error("api", f"API request failed: {exc}")
        return EXIT_API

    if args.command == "submit-indexing":
        code = _submit_exit_code(payload)
        payload = _strip_error_types(payload)
        if code != EXIT_OK:
            # Partial failure: the results JSON still goes to stdout (plan 2.3).
            _print_payload(args, payload)
            if code == EXIT_PERMISSION:
                emit_error("permission", _permission_message(None))
            else:
                emit_error("api", "some URLs failed to submit")
            return code

    _print_payload(args, payload)
    return EXIT_OK


def _permission_message(exc: Exception | None) -> str:
    detail = f"permission denied by Google: {exc} — " if exc is not None else ""
    return (
        f"{detail}the service account lacks permission for this operation on "
        "the site (a read-only account cannot submit indexing; submitting "
        "URLs requires a GSC owner)"
    )


def _print_payload(args: argparse.Namespace, payload: dict) -> None:
    if args.format == "table":
        print(_render_table(args.command, payload))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
