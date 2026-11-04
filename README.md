# gsc-cli

`gsc` is a command-line tool for Google Search Console: read-only queries print
JSON by default, and the only write operation (`submit-indexing`) requires an
explicit `--write`. The package also ships `gsc-mcp`, the MCP server previously
distributed as `gsc-mcp` — same tools, same output, now a thin wrapper around
the same core.

## Install (Linux/macOS, via uv)

```sh
uv tool install git+https://github.com/shaomingbo/gsc-cli
```

Or run it once without installing:

```sh
uvx --from git+https://github.com/shaomingbo/gsc-cli gsc sites
```

## Credentials

A service-account JSON key is required. The path is taken from
`GSC_CREDENTIALS_PATH`, defaulting to `~/.config/gsc-credentials.json`.
Query commands (`sites`, `performance`, `sitemaps`, `inspect-url`) only
request the `webmasters.readonly` scope; `submit-indexing` requests the
`indexing` scope.

## Commands (one example each)

```sh
gsc sites
gsc performance feeprobe.com --start 2026-10-01 --end 2026-10-07
gsc sitemaps feeprobe.com
gsc inspect-url feeprobe.com https://feeprobe.com/
gsc submit-indexing https://feeprobe.com/ --write
```

Add `--format table` (before the subcommand) for aligned plain-text output.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 2 | usage error (bad arguments, or `submit-indexing` without `--write`) |
| 3 | credentials error (file missing or unparseable) |
| 4 | permission denied (HTTP 401/403; submitting requires a GSC owner) |
| 5 | API/network failure |

Failures print a single JSON line to stderr, e.g.
`{"error": {"type": "usage", "message": "submit-indexing is a write operation; pass --write to execute it"}}`.

## MCP usage (gsc-mcp)

The MCP server is unchanged and compatible with existing `gsc-mcp` setups:
command `gsc-mcp` (stdio), tools `gsc_performance`, `gsc_submit_indexing`,
`gsc_inspect_url`, `gsc_sitemaps`. Legacy Python imports such as
`gsc_mcp.auth.get_gsc_service` keep working.

## Migrating from the old gsc-mcp tool

```sh
uv tool uninstall gsc-mcp
uv tool install git+https://github.com/shaomingbo/gsc-cli
```
