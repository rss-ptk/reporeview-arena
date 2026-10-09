"""Evidence-first scorecard; only human-validated findings earn points."""

from __future__ import annotations

from typing import Any

WEIGHTS = {"P0": 20, "P1": 10, "P2": 4, "P3": 1}


def calculate(report: dict[str, Any], judgment: dict[str, Any]) -> dict[str, Any]:
    adjudications = judgment.get("findings", {})
    bugs = 0
    penalty = 0
    rewarded_issues: set[str] = set()
    accepted = 0
    rejected = 0
    pending = 0
    for finding in report["findings"]:
        decision = adjudications.get(finding["id"], {})
        verdict = decision.get("verdict", "pending")
        if verdict == "confirmed":
            # Deduplicate within this model by *explicit human issue key*.
            issue = str(decision.get("issue_key") or finding["id"])
            if issue not in rewarded_issues:
                severity = decision.get("severity", finding["severity"])
                bugs += WEIGHTS[severity]
                rewarded_issues.add(issue)
            accepted += 1
        elif verdict == "false_positive":
            penalty += 10 if decision.get("fabricated_evidence") else 5
            rejected += 1
        else:
            pending += 1
    bugs = min(bugs, 60)
    rubric = judgment.get("rubric", {})
    improvements = min(max(int(rubric.get("improvements", 0)), 0), 15)
    gold = min(max(int(rubric.get("gold", 0)), 0), 20)
    rigor = min(max(int(rubric.get("rigor", 0)), 0), 5)
    total = max(0, min(100, bugs + improvements + gold + rigor - penalty))
    return {
        "bugs": bugs, "improvements": improvements, "gold": gold, "rigor": rigor,
        "penalty": penalty, "total": total, "confirmed": accepted,
        "false_positives": rejected, "pending": pending,
    }


def leaderboard(reports: dict[str, dict], judgments: dict[str, dict]) -> list[dict]:
    rows = [
        {"model": name, **calculate(report, judgments.get(name, {})),
         "findings": len(report["findings"]),
         "golden_feature": report["golden_feature"]["name"]}
        for name, report in reports.items()
    ]
    return sorted(rows, key=lambda x: (-x["total"], -x["confirmed"], x["model"]))
