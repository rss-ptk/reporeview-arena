"""Persistent, content-free usage records for provider attempts."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .storage import load_json, save_json

TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens",
                "cache_write_tokens", "reasoning_tokens")


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_./:-]{1,256}", value) else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _money(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return str(number) if number.is_finite() and number >= 0 else None


def _signed_money(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return str(number) if number.is_finite() else None


def is_openrouter_endpoint(requested_model: str, api_base: str | None) -> bool:
    if not requested_model.startswith("openrouter/"):
        return False
    if not api_base:
        return True
    try:
        url = urlsplit(api_base)
        return (url.scheme == "https" and url.hostname in {"openrouter.ai", "us.openrouter.ai", "eu.openrouter.ai"}
                and url.port in (None, 443) and url.username is None and url.password is None)
    except ValueError:
        return False


def normalize_response(response: Any, requested_model: str, *, openrouter: bool | None = None) -> dict[str, Any]:
    """Extract only allowlisted metadata; an absent value is never a free call."""
    usage = _get(response, "usage")
    prompt = _get(usage, "prompt_tokens_details")
    completion = _get(usage, "completion_tokens_details")
    is_openrouter = is_openrouter_endpoint(requested_model, None) if openrouter is None else openrouter
    cost = _money(_get(usage, "cost")) if is_openrouter else None
    source = "response" if cost is not None else None
    if is_openrouter and cost is None:
        # This specific header is populated from OpenRouter's response body by LiteLLM.
        hidden = _get(_get(response, "_hidden_params"), "additional_headers")
        cost = _money(_get(hidden, "llm_provider-x-litellm-response-cost"))
        if cost is not None:
            source = "litellm_openrouter_header"
    details = _get(usage, "cost_details")
    cache_discount = _signed_money(_get(usage, "cache_discount")) if is_openrouter else None
    cache_discount_source = "usage" if cache_discount is not None else None
    if is_openrouter and cache_discount is None:
        cache_discount = _signed_money(_get(response, "cache_discount"))
        if cache_discount is not None:
            cache_discount_source = "response"
    return {
        "generation_id": _identifier(_get(response, "id")),
        "resolved_model": _identifier(_get(response, "model")),
        "provider": _identifier(_get(response, "provider")),
        "is_byok": _get(usage, "is_byok") if type(_get(usage, "is_byok")) is bool else None,
        "usage": {
            "prompt_tokens": _count(_get(usage, "prompt_tokens")),
            "completion_tokens": _count(_get(usage, "completion_tokens")),
            "total_tokens": _count(_get(usage, "total_tokens")),
            "cached_tokens": _count(_get(prompt, "cached_tokens")),
            "cache_write_tokens": _count(_get(prompt, "cache_write_tokens")),
            "reasoning_tokens": _count(_get(completion, "reasoning_tokens")),
        },
        "cost": {"openrouter_credits_usd": cost,
                 "upstream_inference_usd": _money(_get(details, "upstream_inference_cost")) if is_openrouter else None,
                 "source": source, "cache_discount_usd": cache_discount,
                 "cache_discount_source": cache_discount_source},
    }


def new_run_id() -> str:
    return uuid.uuid4().hex


def attempt_path(workspace: Path, run_id: str, model_id: str, step: int, attempt: int) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", model_id):
        raise ValueError("Invalid usage identifier")
    return workspace / "usage" / run_id / model_id / f"{step:03d}-{attempt:02d}.json"


def start_attempt(workspace: Path, run_id: str, model_id: str, model: str,
                  step: int, attempt: int, api_base: str | None = None,
                  session_id: str | None = None) -> tuple[Path, dict[str, Any]]:
    path = attempt_path(workspace, run_id, model_id, step, attempt)
    openrouter_verified = is_openrouter_endpoint(model, api_base)
    record = {"schema_version": 1, "run_id": run_id, "model_id": model_id,
              "requested_model": _identifier(model), "openrouter_verified": openrouter_verified,
              "session_id": _identifier(session_id) if openrouter_verified else None,
              "step": step, "attempt": attempt,
              "started_at": datetime.now(timezone.utc).isoformat(), "status": "in_progress",
              "generation_id": None, "resolved_model": None, "provider": None, "is_byok": None,
              "usage": {field: None for field in TOKEN_FIELDS},
              "cost": {"openrouter_credits_usd": None, "upstream_inference_usd": None,
                       "source": None, "cache_discount_usd": None,
                       "cache_discount_source": None}}
    save_json(path, record)
    return path, record


def finish_attempt(path: Path, record: dict[str, Any], *, response: Any = None) -> None:
    record["status"] = "response_received" if response is not None else "failed"
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    if response is not None:
        record.update(normalize_response(response, record["requested_model"] or "",
                                         openrouter=record["openrouter_verified"]))
    save_json(path, record)


def summarize(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    coverage: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        values = [_count(item.get("usage", {}).get(field)) for item in attempts]
        present = [v for v in values if v is not None]
        totals[field] = sum(present) if present else None
        coverage[field] = len(present)
    costs = [_money(item.get("cost", {}).get("openrouter_credits_usd")) for item in attempts]
    known = [Decimal(value) for value in costs if value is not None]
    cache_pairs = [(item.get("usage", {}).get("cached_tokens"), item.get("usage", {}).get("prompt_tokens"))
                   for item in attempts]
    cache_pairs = [(cached, prompt) for cached, prompt in cache_pairs
                   if _count(cached) is not None and _count(prompt) is not None and cached <= prompt]
    cache_prompt = sum(prompt for _, prompt in cache_pairs)
    cache_read = sum(cached for cached, _ in cache_pairs)
    totals.update({"attempts": len(attempts), "known_cost_attempts": len(known),
                   "unknown_cost_attempts": len(attempts) - len(known),
                   "openrouter_credits_usd": str(sum(known, Decimal(0))) if known else None,
                   "cost_status": ("unavailable" if not known else
                                   "complete" if len(known) == len(attempts) else "partial"),
                   "token_coverage": coverage, "cache_rate_attempts": len(cache_pairs),
                   "cache_rate": str(Decimal(cache_read) / Decimal(cache_prompt)) if cache_prompt else None})
    cache_discounts = [_signed_money(item.get("cost", {}).get("cache_discount_usd"))
                       for item in attempts]
    known_cache_discounts = [Decimal(value) for value in cache_discounts if value is not None]
    totals.update({"known_cache_discount_attempts": len(known_cache_discounts),
                   "unknown_cache_discount_attempts": len(attempts) - len(known_cache_discounts),
                   "cache_discount_usd": (str(sum(known_cache_discounts, Decimal(0)))
                                           if known_cache_discounts else None),
                   "cache_discount_status": ("unavailable" if not known_cache_discounts else
                                             "complete" if len(known_cache_discounts) == len(attempts)
                                             else "partial")})
    return totals


def read_usage(workspace: Path, run_id: str | None = None) -> dict[str, Any]:
    if run_id is not None and not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid run ID")
    root = workspace / "usage"
    runs = []
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
            continue
        if run_id is not None and directory.name != run_id:
            continue
        attempts = [load_json(p) for p in sorted(directory.glob("*/*.json")) if not p.is_symlink()]
        models = {model: summarize([item for item in attempts if item.get("model_id") == model])
                  for model in sorted({item.get("model_id") for item in attempts if isinstance(item.get("model_id"), str)})}
        runs.append({"run_id": directory.name, "summary": summarize(attempts),
                     "models": models, "attempts": attempts})
    runs.sort(key=lambda run: max((a.get("started_at", "") for a in run["attempts"]), default=""), reverse=True)
    return {"runs": runs}
