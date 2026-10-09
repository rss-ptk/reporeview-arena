"""Structured report import and normalization."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REQUIRED_FINDING = (
    "title", "severity", "file", "line", "description", "evidence", "recommendation",
)
SEVERITIES = ("P0", "P1", "P2", "P3")
EVIDENCE = ("confirmed", "strong_evidence", "hypothesis")


def _decode(source: str) -> Any:
    # Support JSON from API models or fenced blocks copied from an editor.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", source, re.DOTALL | re.I)
    if fenced:
        source = fenced.group(1)
    try:
        return json.loads(source.strip())
    except json.JSONDecodeError:
        begin = source.find("{")
        if begin == -1:
            raise ValueError("No JSON object in report") from None
        try:
            value, _ = json.JSONDecoder().raw_decode(source[begin:])
            return value
        except json.JSONDecodeError as exc:
            raise ValueError("Report must contain valid JSON with the required schema") from exc


def parse_report(source: str, known_files: set[str]) -> dict[str, Any]:
    report = _decode(source)
    if not isinstance(report, dict):
        raise ValueError("Report must be a JSON object")
    if not isinstance(report.get("findings"), list):
        raise ValueError("Report must contain a findings array")
    for attr in ("coverage", "limitations", "test_gaps", "improvements"):
        if attr not in report:
            report[attr] = []
        if not isinstance(report[attr], list):
            raise ValueError(f"{attr} must be an array")
    for attr in ("summary", "hotpath"):
        report.setdefault(attr, "")
        if not isinstance(report[attr], str):
            raise ValueError(f"{attr} must be a string")
    gold = report.get("golden_feature")
    if not isinstance(gold, dict) or not str(gold.get("name", "")).strip():
        raise ValueError("Report requires one golden_feature with a name")
    ids: set[str] = set()
    for i, f in enumerate(report["findings"], 1):
        if not isinstance(f, dict):
            raise ValueError(f"Finding {i} must be an object")
        for key in REQUIRED_FINDING:
            if key not in f:
                raise ValueError(f"Finding {i} is missing {key}")
        f.setdefault("id", f"FS-AUD-{i:03d}")
        if not isinstance(f["id"], str) or f["id"] in ids:
            raise ValueError("Finding IDs must be unique nonempty strings")
        ids.add(f["id"])
        if f["severity"] not in SEVERITIES:
            raise ValueError(f"Unknown severity: {f['severity']}")
        f.setdefault("evidence_status", "hypothesis")
        if f["evidence_status"] not in EVIDENCE:
            raise ValueError(f"Unknown evidence status: {f['evidence_status']}")
        if not isinstance(f["line"], int) or isinstance(f["line"], bool) or f["line"] < 1:
            raise ValueError("Finding line must be a positive integer")
        if f["file"] not in known_files:
            raise ValueError(f"Finding references a file outside snapshot: {f['file']}")
        try:
            f["confidence"] = float(f.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid confidence") from exc
        if not 0 <= f["confidence"] <= 1:
            raise ValueError("Confidence must be between 0 and 1")
        f.setdefault("scenario", "")
        f.setdefault("impact", "")
        f.setdefault("invariant", "")
        f.setdefault("regression_test", "")
    for improvement in report["improvements"]:
        if not isinstance(improvement, dict) or not improvement.get("title"):
            raise ValueError("Each improvement must have a title")
    return report


def validate_locations(report: dict[str, Any], snapshot: Path) -> None:
    """Require line citations to refer to existing lines in frozen source files."""

    for finding in report["findings"]:
        path = snapshot / finding["file"]
        if path.is_symlink() or not path.resolve().is_relative_to(snapshot.resolve()):
            raise ValueError("Finding file escapes snapshot")
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if finding["line"] > line_count:
            raise ValueError(
                f"Finding {finding['id']} cites {finding['file']} line {finding['line']}, "
                f"but the file has only {line_count} lines"
            )
