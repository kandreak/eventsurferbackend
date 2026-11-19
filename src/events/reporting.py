from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from events.models import Event


def _text_report(report: dict[str, Any]) -> str:
    lines = [
        "EVENTS AGGREGATOR REPORT",
        "========================",
        f"Sources configured: {report['sources_configured']}",
        f"Sources collected: {report['sources_succeeded']}",
        f"Posts retrieved: {report['posts_retrieved']}",
        f"Posts within lookback: {report['posts_in_lookback']}",
        f"Posts skipped: {report['posts_skipped']}",
        f"Posts analyzed: {report['posts_analyzed']}",
        f"Posts reused from cache: {report['posts_cached']}",
        f"Events extracted: {report['events_extracted']}",
        f"Duplicates removed: {report['duplicates_removed']}",
        f"Events requiring review: {report['events_requiring_review']}",
        f"Events without dates (included as undated tasks): {report['events_without_dates']}",
        f"Failed sources: {len(report['source_errors'])}",
        f"Failed extractions: {len(report['post_errors'])}",
        "",
    ]
    if report["source_errors"]:
        lines.append("Source errors:")
        lines.extend(f"- {error}" for error in report["source_errors"])
        lines.append("")
    if report["post_errors"]:
        lines.append("Post errors:")
        lines.extend(f"- {error}" for error in report["post_errors"])
        lines.append("")
    if report["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
    if report["events_without_dates"]:
        lines.append("Events saved as undated tasks:")
        lines.extend(f"- {title}" for title in report["events_without_dates"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(
    *,
    output_directory: Path,
    calendar_filename: str,
    calendar_data: bytes,
    events: list[Event],
    report: dict[str, Any],
    json_enabled: bool,
    text_report_enabled: bool,
) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    calendar_path = output_directory / calendar_filename
    calendar_path.write_bytes(calendar_data)
    written = [calendar_path]

    if json_enabled:
        events_path = output_directory / "events.json"
        events_path.write_text(
            json.dumps(
                [event.model_dump(mode="json") for event in events],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(events_path)

    if text_report_enabled:
        report_path = output_directory / "report.txt"
        report_path.write_text(_text_report(report), encoding="utf-8")
        written.append(report_path)
    return written
