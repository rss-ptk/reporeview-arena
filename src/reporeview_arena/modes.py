"""Shared contract for the independent review deliverables."""

from __future__ import annotations

REVIEW_MODES = ("code_audit", "golden_feature")
DEFAULT_REVIEW_MODES = REVIEW_MODES


def normalize_review_modes(value: object = None) -> list[str]:
    """Validate a review's selected deliverables; missing values mean legacy both-mode."""

    if value is None:
        return list(DEFAULT_REVIEW_MODES)
    if not isinstance(value, list) or any(not isinstance(mode, str) for mode in value):
        raise ValueError("review_modes must be an array of mode IDs")
    if not value:
        raise ValueError("review_modes must include at least one mode")
    if len(set(value)) != len(value):
        raise ValueError("review_modes must not contain duplicates")
    unknown = set(value) - set(REVIEW_MODES)
    if unknown:
        raise ValueError("review_modes contains an unknown mode")
    return [mode for mode in REVIEW_MODES if mode in value]


def rubric_fields_for_modes(review_modes: object = None) -> tuple[str, ...]:
    """Return rubric fields that belong to the selected review deliverables."""

    modes = normalize_review_modes(review_modes)
    fields: list[str] = []
    if "code_audit" in modes:
        fields.extend(("improvements", "rigor"))
    if "golden_feature" in modes:
        fields.append("gold")
    return tuple(fields)
