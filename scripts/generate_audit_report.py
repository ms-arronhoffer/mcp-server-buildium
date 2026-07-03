#!/usr/bin/env python3
"""Generate an audit report from the Buildium MCP server's file audit sink.

Reads the newline-delimited JSON audit trail produced when
``BUILDIUM_AUDIT_SINK=file`` is configured and renders a human-readable summary:
counts by tool, outcome, and operation type; the overall error rate; recent
mutations; and recent denied/rate-limited (security-relevant) attempts.

Usage::

    python scripts/generate_audit_report.py AUDIT_FILE [--format md|csv] [--limit N]
    python scripts/generate_audit_report.py audit.log --output docs/audit-report.md
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from mcp_server_buildium import audit  # noqa: E402


def render_markdown(summary: dict, source: str) -> str:
    """Render the audit summary as Markdown."""
    lines: list[str] = []
    lines.append("# Audit Report")
    lines.append("")
    lines.append(f"Source: `{source}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total events: **{summary['total_events']}**")
    lines.append(f"- Error rate: **{summary['error_rate']:.2%}**")
    lines.append(f"- Mutations: **{summary['mutation_count']}**")
    lines.append(f"- Denied / rate-limited: **{summary['denied_count']}**")
    lines.append("")

    lines.append("## By outcome")
    lines.append("")
    lines.append("| Outcome | Count |")
    lines.append("| --- | --- |")
    for outcome, count in sorted(summary["by_outcome"].items()):
        lines.append(f"| {outcome} | {count} |")
    lines.append("")

    lines.append("## By operation type")
    lines.append("")
    lines.append("| Operation type | Count |")
    lines.append("| --- | --- |")
    for op_type, count in sorted(summary["by_op_type"].items()):
        lines.append(f"| {op_type} | {count} |")
    lines.append("")

    lines.append("## By tool")
    lines.append("")
    lines.append("| Tool | Count |")
    lines.append("| --- | --- |")
    for tool, count in summary["by_tool"].items():
        lines.append(f"| `{tool}` | {count} |")
    lines.append("")

    if summary["recent_denied"]:
        lines.append("## Recent denied / rate-limited attempts")
        lines.append("")
        lines.append("| Timestamp | Tool | Outcome | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for event in summary["recent_denied"]:
            lines.append(
                f"| {event.get('timestamp', '')} | `{event.get('tool', '')}` | "
                f"{event.get('outcome', '')} | {event.get('reason', '')} |"
            )
        lines.append("")

    if summary["recent_mutations"]:
        lines.append("## Recent mutations")
        lines.append("")
        lines.append("| Timestamp | Tool | Outcome | Status |")
        lines.append("| --- | --- | --- | --- |")
        for event in summary["recent_mutations"]:
            lines.append(
                f"| {event.get('timestamp', '')} | `{event.get('tool', '')}` | "
                f"{event.get('outcome', '')} | {event.get('status', '')} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def render_csv(summary: dict) -> str:
    """Render per-tool counts as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["tool", "count"])
    for tool, count in summary["by_tool"].items():
        writer.writerow([tool, count])
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_file", help="Path to the newline-delimited JSON audit log.")
    parser.add_argument(
        "--format", choices=("md", "csv"), default="md", help="Output format (default: md)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only summarize the most recent N records.",
    )
    parser.add_argument(
        "--output",
        help="Write the report to this path instead of stdout.",
    )
    args = parser.parse_args()

    events = audit.read_events(args.audit_file, limit=args.limit)
    summary = audit.summarize_events(events)
    report = (
        render_csv(summary) if args.format == "csv" else render_markdown(summary, args.audit_file)
    )

    if args.output:
        Path(args.output).write_text(report)
        print(f"Wrote {args.output} ({summary['total_events']} events).")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
