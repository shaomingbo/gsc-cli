"""GSC MCP Server — exposes Google Search Console data as MCP tools."""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP

from gsc_mcp.auth import get_gsc_service, get_indexing_service, get_webmasters_service

mcp = FastMCP(
    "GSC",
    instructions="""\
Google Search Console MCP server. Provides 4 tools: gsc_performance (search \
analytics), gsc_submit_indexing (URL indexing), gsc_inspect_url (URL inspection), \
gsc_sitemaps (sitemap listing).

IMPORTANT LIMITATIONS — the following data is NOT available via API and can only \
be viewed in the GSC web interface (https://search.google.com/search-console):
- Coverage overview: total indexed / not-indexed page counts
- Not-indexed reason breakdown (discovered but not indexed, crawled but not indexed, etc.)
- Redirect counts and details
- CWV (Core Web Vitals) aggregate reports

When your task requires coverage or indexing status data beyond single-URL inspection, \
proactively ask the user to provide it from the GSC web interface. Do not assume \
this data can be retrieved programmatically.\
""",
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_DIMENSIONS = {"query", "page", "date", "country", "device"}


def _normalise_site(site: str) -> str:
    """Ensure *site* uses the format the API expects.

    - Already prefixed with ``sc-domain:`` or ``https://`` → use as-is.
    - Plain domain like ``saasprobe.com`` → prepend ``sc-domain:``.
    """
    if site.startswith(("sc-domain:", "https://", "http://")):
        return site
    return f"sc-domain:{site}"


def _format_row(row: dict, dimensions: list[str]) -> str:
    keys = row.get("keys", [])
    clicks = row.get("clicks", 0)
    impressions = row.get("impressions", 0)
    ctr = row.get("ctr", 0.0)
    position = row.get("position", 0.0)

    dim_parts = "  |  ".join(
        f"{dim}={keys[i]}" if i < len(keys) else f"{dim}=?"
        for i, dim in enumerate(dimensions)
    )
    return (
        f"{dim_parts}  |  "
        f"clicks={clicks}  impr={impressions}  "
        f"ctr={ctr:.1%}  pos={position:.1f}"
    )


@mcp.tool()
def gsc_performance(
    site: str,
    start_date: str,
    end_date: str,
    dimensions: str = "query",
    row_limit: int = 25,
    page_filter: str | None = None,
    query_filter: str | None = None,
) -> str:
    """Query Google Search Console search-analytics performance data.

    Args:
        site: Domain name (e.g. "saasprobe.com"). Automatically prefixed
              with "sc-domain:" unless already in sc-domain: or https:// form.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        dimensions: Comma-separated dimensions: query, page, date, country, device.
        row_limit: Maximum number of rows to return (default 25).
        page_filter: Optional URL substring filter (e.g. "/compare/").
        query_filter: Optional query keyword filter (e.g. "clickup").
    """
    # --- input validation ---
    if not _DATE_RE.match(start_date):
        return (
            f"Error: start_date '{start_date}' is not valid. "
            "Expected format: YYYY-MM-DD (e.g. 2026-01-15)."
        )
    if not _DATE_RE.match(end_date):
        return (
            f"Error: end_date '{end_date}' is not valid. "
            "Expected format: YYYY-MM-DD (e.g. 2026-01-15)."
        )

    dim_list = [d.strip() for d in dimensions.split(",")]
    invalid_dims = [d for d in dim_list if d not in _VALID_DIMENSIONS]
    if invalid_dims:
        return (
            f"Error: invalid dimension(s): {', '.join(invalid_dims)}. "
            f"Allowed values: {', '.join(sorted(_VALID_DIMENSIONS))}."
        )

    if not (1 <= row_limit <= 25000):
        return f"Error: row_limit must be between 1 and 25000, got {row_limit}."

    site_url = _normalise_site(site)

    body: dict = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dim_list,
        "rowLimit": row_limit,
    }

    # Build optional dimension filters.
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
        service = get_gsc_service()
        response = (
            service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        )
    except SystemExit:
        return "Error: authentication failed. Check GSC_CREDENTIALS_PATH."
    except Exception as exc:  # noqa: BLE001
        return f"Error: API request failed — {exc}"

    rows = response.get("rows", [])
    if not rows:
        return (
            f"No data for {site_url} from {start_date} to {end_date} "
            f"(dimensions={dimensions})."
        )

    header = (
        f"GSC Performance: {site_url}\n"
        f"Period: {start_date} → {end_date}\n"
        f"Dimensions: {', '.join(dim_list)}  |  Rows: {len(rows)}\n"
        f"{'-' * 60}"
    )
    lines = [header] + [_format_row(row, dim_list) for row in rows]
    return "\n".join(lines)


@mcp.tool()
def gsc_submit_indexing(
    urls: str,
    action: str = "URL_UPDATED",
) -> str:
    """Submit URLs to the Google Indexing API for crawling or removal.

    Args:
        urls: Comma-separated list of URLs to submit
              (e.g. "https://example.com/page1,https://example.com/page2").
        action: "URL_UPDATED" (request crawl) or "URL_DELETED" (request removal).
    """
    if action not in ("URL_UPDATED", "URL_DELETED"):
        return f"Error: action must be URL_UPDATED or URL_DELETED, got '{action}'"

    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        return "Error: no URLs provided."

    invalid_urls = [u for u in url_list if not u.startswith(("http://", "https://"))]
    if invalid_urls:
        return (
            f"Error: each URL must start with http:// or https://. "
            f"Invalid: {', '.join(invalid_urls)}"
        )

    try:
        service = get_indexing_service()
    except SystemExit:
        return "Error: authentication failed. Check GSC_CREDENTIALS_PATH."

    results: list[str] = []
    for url in url_list:
        body = {"url": url, "type": action}
        try:
            response = service.urlNotifications().publish(body=body).execute()
            notif = response.get("urlNotificationMetadata", {})
            latest = notif.get("latestUpdate", {}) or notif.get("latestRemove", {})
            notify_time = latest.get("notifyTime", "unknown")
            results.append(f"OK  {url}  (notifyTime={notify_time})")
        except Exception as exc:  # noqa: BLE001
            results.append(f"FAIL  {url}  — {exc}")

    header = (
        f"Indexing API — action={action}  |  submitted={len(url_list)}\n{'-' * 60}\n"
    )
    footer = (
        f"\n{'-' * 60}\n"
        "Note: daily quota is ~200 publish calls. "
        "Monitor usage in the Google Cloud Console."
    )
    return header + "\n".join(results) + footer


@mcp.tool()
def gsc_inspect_url(
    site: str,
    url: str,
) -> str:
    """Inspect a URL using Google Search Console URL Inspection API.

    Args:
        site: Domain name (e.g. "saasprobe.com"). Automatically normalized.
        url: Full URL to inspect (e.g. "https://saasprobe.com/reviews/notion-review-2026/").
    """
    if not url.startswith(("http://", "https://")):
        return f"Error: url must start with http:// or https://, got '{url}'."

    site_url = _normalise_site(site)

    try:
        service = get_webmasters_service()
    except SystemExit:
        return "Error: authentication failed. Check GSC_CREDENTIALS_PATH."

    body = {
        "inspectionUrl": url,
        "siteUrl": site_url,
    }

    try:
        response = service.urlInspection().index().inspect(body=body).execute()
    except Exception as exc:  # noqa: BLE001
        return f"Error: URL Inspection request failed — {exc}"

    result = response.get("inspectionResult", {})
    index_status = result.get("indexStatusResult", {})
    mobile = result.get("mobileUsabilityResult", {})
    rich = result.get("richResultsResult", {})

    lines = [
        f"URL Inspection: {url}",
        f"Site: {site_url}",
        f"{'-' * 60}",
        f"Verdict: {index_status.get('verdict', 'N/A')}",
        f"Coverage state: {index_status.get('coverageState', 'N/A')}",
        f"Indexing state: {index_status.get('indexingState', 'N/A')}",
        f"Robots.txt state: {index_status.get('robotsTxtState', 'N/A')}",
        f"Page fetch state: {index_status.get('pageFetchState', 'N/A')}",
        f"Google canonical: {index_status.get('googleCanonical', 'N/A')}",
        f"User canonical: {index_status.get('userCanonical', 'N/A')}",
        f"Last crawl time: {index_status.get('lastCrawlTime', 'N/A')}",
        f"Crawled as: {index_status.get('crawledAs', 'N/A')}",
        f"Referring sitemaps: {', '.join(index_status.get('sitemap', [])) or 'N/A'}",
        f"{'-' * 60}",
        f"Mobile usability: {mobile.get('verdict', 'N/A')}",
        f"Rich results: {rich.get('verdict', 'N/A')}",
    ]
    return "\n".join(lines)


@mcp.tool()
def gsc_sitemaps(
    site: str,
) -> str:
    """List sitemaps registered in Google Search Console for a site.

    Args:
        site: Domain name (e.g. "saasprobe.com"). Automatically normalized.
    """
    site_url = _normalise_site(site)

    try:
        service = get_gsc_service()
    except SystemExit:
        return "Error: authentication failed. Check GSC_CREDENTIALS_PATH."

    try:
        response = service.sitemaps().list(siteUrl=site_url).execute()
    except Exception as exc:  # noqa: BLE001
        return f"Error: sitemaps request failed — {exc}"

    sitemaps = response.get("sitemap", [])
    if not sitemaps:
        return f"No sitemaps found for {site_url}."

    lines = [
        f"Sitemaps for {site_url}  |  Count: {len(sitemaps)}",
        f"{'-' * 60}",
    ]
    for sm in sitemaps:
        path = sm.get("path", "N/A")
        sm_type = sm.get("type", "N/A")
        submitted = sm.get("lastSubmitted", "N/A")
        downloaded = sm.get("lastDownloaded", "N/A")
        is_pending = sm.get("isPending", False)
        warnings_count = sm.get("warnings", "0")
        errors_count = sm.get("errors", "0")

        # Content details (URL counts per type).
        contents = sm.get("contents", [])
        url_counts = []
        for c in contents:
            c_type = c.get("type", "unknown")
            c_submitted = c.get("submitted", "?")
            c_indexed = c.get("indexed", "?")
            url_counts.append(
                f"{c_type}: {c_submitted} submitted / {c_indexed} indexed"
            )

        lines.append(f"  Path: {path}")
        lines.append(f"  Type: {sm_type}")
        lines.append(f"  Last submitted: {submitted}")
        lines.append(f"  Last downloaded: {downloaded}")
        lines.append(
            f"  Pending: {is_pending}  |  Warnings: {warnings_count}  |  Errors: {errors_count}"
        )
        if url_counts:
            lines.append(f"  URLs: {' | '.join(url_counts)}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Entry-point used by the ``gsc-mcp`` console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
