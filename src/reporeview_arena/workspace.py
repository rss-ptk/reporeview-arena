"""Operations that create and validate local audit workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .prompts import build_prompt
from .repository import RepositoryReader, snapshot_repo
from .storage import save_json

DEFAULT_FOCUS = (
    "Audit the most consequential hotpaths, correctness, failure recovery, security, "
    "and major architecture gaps. Focus on what can cause real harm."
)

REVIEW_FOCUS_CRITERIA = {
    "critical_flows": ("Critical flows and impact", 5),
    "correctness": ("Correctness and edge cases", 5),
    "security": ("Security and privacy", 5),
    "failure_recovery": ("Failure handling and recovery", 4),
    "data_integrity": ("Data integrity and consistency", 3),
    "architecture": ("Architecture and maintainability", 3),
    "performance": ("Performance and resource use", 2),
    "api_compatibility": ("APIs and compatibility", 2),
    "testing_observability": ("Testing and observability", 2),
}

REVIEW_FOCUS_INSTRUCTIONS = {
    "critical_flows": (
        "Map primary user journeys from entry point to outcome and side effects; identify failure "
        "points, affected users or data, and impact on core operations."
    ),
    "correctness": (
        "Check invariants across state transitions, validation, boundaries, empty or unexpected "
        "inputs, and concurrency; give a concrete scenario that violates expected behavior."
    ),
    "security": (
        "Trace authentication, authorization, trust boundaries, secrets, tenant isolation, and "
        "sensitive-data flows; explain a plausible exposure or exploit path and impact."
    ),
    "failure_recovery": (
        "Inspect exceptions, timeouts, retries, cancellation, partial failure, rollback, and "
        "restart; verify recovery is bounded and avoids lost or duplicated work."
    ),
    "data_integrity": (
        "Trace state across transactions and storage layers; check atomicity, ordering, duplicate "
        "updates, stale reads, migrations, and concrete loss or corruption paths."
    ),
    "architecture": (
        "Inspect module boundaries, dependency direction, coupling, duplicated policy, and hidden "
        "contracts; recommend structural changes only for a concrete risk or cost."
    ),
    "performance": (
        "Trace hot-path algorithm and query cost, I/O, memory, and resource lifetimes; identify a "
        "plausible workload that exposes a measurable bottleneck."
    ),
    "api_compatibility": (
        "Inspect public contracts, validation, serialization, errors, versioning, and callers; "
        "identify behavior changes that could break existing consumers."
    ),
    "testing_observability": (
        "Check deterministic tests for important paths and failures, plus whether logs, metrics, "
        "and traces expose regressions; name specific coverage or diagnosis gaps."
    ),
}

FOCUS_PRIORITY_LABELS = {
    1: "low",
    2: "medium",
    3: "high",
    4: "very high",
    5: "critical",
}
MAX_CUSTOM_FOCUS_LENGTH = 4000
MAX_FOCUS_LENGTH = 6200


def format_review_focus(criteria: object, custom_text: object) -> str:
    """Validate and render the dashboard's structured review priorities."""

    if not isinstance(criteria, dict):
        raise ValueError("review_focus.criteria must be an object")
    unknown = set(criteria) - REVIEW_FOCUS_CRITERIA.keys()
    if unknown:
        raise ValueError("review_focus.criteria contains an unknown criterion")
    if not isinstance(custom_text, str):
        raise ValueError("review_focus.custom_text must be text")
    if len(custom_text) > MAX_CUSTOM_FOCUS_LENGTH:
        raise ValueError("custom criteria must be no longer than 4000 characters")

    selected: list[str] = []
    for criterion_id, (label, _default) in REVIEW_FOCUS_CRITERIA.items():
        value = criteria.get(criterion_id, 0)
        if type(value) is not int or not 0 <= value <= 5:
            raise ValueError("criterion priorities must be whole numbers from 0 to 5")
        if value:
            selected.append(
                f"- {label} — priority {value}/5 ({FOCUS_PRIORITY_LABELS[value]}): "
                f"{REVIEW_FOCUS_INSTRUCTIONS[criterion_id]}"
            )

    if not selected and not custom_text.strip():
        return "No additional priorities selected; follow the general audit instructions."

    sections = [
        "Review priorities (higher values mean greater emphasis; evidence standards remain unchanged):",
        *selected,
    ]
    if custom_text.strip():
        sections.extend(("Custom criteria (one per line):", custom_text))
    focus = "\n".join(sections)
    if len(focus) > MAX_FOCUS_LENGTH:
        raise ValueError("combined review focus must be no longer than 6000 characters")
    return focus


def initialize_workspace(
    repo: Path,
    destination: Path,
    focus: str,
    *,
    review_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Freeze a clean repository and create its manifest and reusable prompt."""

    manifest = snapshot_repo(repo, destination)
    if review_id is not None:
        manifest["review_id"] = review_id
    if created_at is not None:
        manifest["created_at"] = created_at
    save_json(destination / "manifest.json", manifest)
    reader = RepositoryReader(destination / "snapshot")
    prompt = build_prompt(manifest, focus, reader.describe())
    (destination / "prompt.md").write_text(prompt, encoding="utf-8")
    return manifest


def validate_workspace(workspace: Path) -> Path:
    """Return a resolved workspace directory with a readable manifest."""

    workspace = workspace.resolve()
    if not workspace.is_dir() or not (workspace / "manifest.json").is_file():
        raise ValueError("Workspace not initialized; select a folder with manifest.json")
    return workspace
