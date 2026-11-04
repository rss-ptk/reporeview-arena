from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from reporeview_arena.usage import normalize_response, read_usage, summarize


def test_normalizes_openrouter_usage_without_double_counting():
    response = {
        "id": "gen-123", "model": "resolved/model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                  "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 10},
                  "completion_tokens_details": {"reasoning_tokens": 5},
                  "cost": 0, "cost_details": {"upstream_inference_cost": 0.0002}},
    }
    data = normalize_response(response, "openrouter/example/model")
    assert data["usage"]["cached_tokens"] == 60
    assert data["usage"]["reasoning_tokens"] == 5
    assert data["usage"]["total_tokens"] == 120
    assert data["cost"]["openrouter_credits_usd"] == "0"
    assert data["cost"]["upstream_inference_usd"] == "0.0002"
    assert data["generation_id"] == "gen-123"


@pytest.mark.parametrize(("response", "expected", "source"), [
    ({"usage": {"cache_discount": 0.0125}}, "0.0125", "usage"),
    ({"usage": {}, "cache_discount": 0.0125}, "0.0125", "response"),
    ({"usage": {"cache_discount": 0}}, "0", "usage"),
    ({"usage": {"cache_discount": -0.0004}}, "-0.0004", "usage"),
])
def test_normalizes_signed_cache_discount(response, expected, source):
    result = normalize_response(response, "openrouter/example/model")

    assert result["cost"]["cache_discount_usd"] == expected
    assert result["cost"]["cache_discount_source"] == source


def test_cache_discount_normalizes_litellm_object_and_is_openrouter_only():
    response = SimpleNamespace(usage=SimpleNamespace(cache_discount=-0.001))

    assert normalize_response(response, "openrouter/example/model")["cost"]["cache_discount_usd"] == "-0.001"
    assert normalize_response(response, "openai/example-model")["cost"]["cache_discount_usd"] is None


def test_missing_cache_discount_remains_unknown():
    result = normalize_response({"usage": {}}, "openrouter/example/model")

    assert result["cost"]["cache_discount_usd"] is None
    assert result["cost"]["cache_discount_source"] is None


def test_cache_discount_summary_keeps_signed_decimal_and_coverage():
    attempts = [
        {"cost": {"cache_discount_usd": "0.01001", "openrouter_credits_usd": "0.1"}},
        {"cost": {"cache_discount_usd": "-0.002", "openrouter_credits_usd": "0.2"}},
        {"cost": {"cache_discount_usd": None, "openrouter_credits_usd": None}},
    ]

    result = summarize(attempts)

    assert result["cache_discount_usd"] == "0.00801"
    assert result["known_cache_discount_attempts"] == 2
    assert result["unknown_cache_discount_attempts"] == 1
    assert result["cache_discount_status"] == "partial"
    assert result["openrouter_credits_usd"] == "0.3"


def test_only_openrouter_hidden_cost_is_accepted():
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
                               _hidden_params={"additional_headers": {
                                   "llm_provider-x-litellm-response-cost": 0.001}})
    assert normalize_response(response, "openrouter/vendor/model")["cost"]["openrouter_credits_usd"] == "0.001"
    assert normalize_response(response, "openai/model")["cost"]["openrouter_credits_usd"] is None


def test_custom_endpoint_does_not_claim_openrouter_cost(tmp_path):
    from reporeview_arena.usage import finish_attempt, new_run_id, start_attempt

    path, record = start_attempt(tmp_path, new_run_id(), "m", "openrouter/model", 1, 1,
                                 api_base="https://proxy.example/v1")
    finish_attempt(path, record, response={"usage": {"cost": 0.1}})
    assert json.loads(path.read_text())["cost"]["openrouter_credits_usd"] is None


def test_installed_litellm_usage_and_hidden_response_cost():
    pytest.importorskip("litellm")
    from litellm.types.utils import ModelResponse, Usage

    response = ModelResponse(usage=Usage(prompt_tokens=100, completion_tokens=25,
                                         prompt_tokens_details={"cached_tokens": 40},
                                         completion_tokens_details={"reasoning_tokens": 10}))
    response._hidden_params["additional_headers"] = {"llm_provider-x-litellm-response-cost": 0.000123}
    result = normalize_response(response, "openrouter/vendor/model")
    assert result["cost"]["openrouter_credits_usd"] == "0.000123"
    assert result["usage"]["cached_tokens"] == 40
    assert result["usage"]["reasoning_tokens"] == 10


def test_installed_litellm_transforms_openrouter_response_offline():
    pytest.importorskip("litellm")
    import httpx
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig
    from litellm.types.utils import ModelResponse

    raw = {"id": "gen-123", "model": "vendor/model", "choices": [{"index": 0,
           "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                     "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 5},
                     "completion_tokens_details": {"reasoning_tokens": 10},
                     "cost": 0.0012, "cache_discount": -0.0003,
                     "cost_details": {"upstream_inference_cost": 0.0009}}}
    response = OpenrouterConfig().transform_response(
        model="vendor/model", raw_response=httpx.Response(200, json=raw),
        model_response=ModelResponse(), logging_obj=SimpleNamespace(post_call=lambda **kwargs: None),
        request_data={}, messages=[], optional_params={}, litellm_params={}, encoding=None,
    )
    result = normalize_response(response, "openrouter/vendor/model")
    assert result["generation_id"] == "gen-123"
    assert result["usage"]["cache_write_tokens"] == 5
    assert result["cost"]["openrouter_credits_usd"] == "0.0012"
    assert result["cost"]["upstream_inference_usd"] == "0.0009"
    assert result["cost"]["cache_discount_usd"] == "-0.0003"

    raw["usage"].pop("cache_discount")
    raw["cache_discount"] = 0.0004
    response = OpenrouterConfig().transform_response(
        model="vendor/model", raw_response=httpx.Response(200, json=raw),
        model_response=ModelResponse(), logging_obj=SimpleNamespace(post_call=lambda **kwargs: None),
        request_data={}, messages=[], optional_params={}, litellm_params={}, encoding=None,
    )
    result = normalize_response(response, "openrouter/vendor/model")
    assert result["cost"]["cache_discount_usd"] == "0.0004"
    assert result["cost"]["cache_discount_source"] == "response"


def test_installed_litellm_openrouter_transform_forwards_session_id():
    pytest.importorskip("litellm")
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig

    session_id = "reporeview-" + "a" * 32 + "-model"
    body = OpenrouterConfig().transform_request(
        model="vendor/model", messages=[{"role": "user", "content": "hello"}],
        optional_params={"extra_body": {"session_id": session_id}},
        litellm_params={}, headers={},
    )

    assert body["session_id"] == session_id


@pytest.mark.asyncio
async def test_attempts_survive_failures_and_keep_costs_separate(tmp_path, monkeypatch):
    from reporeview_arena.orchestrator import audit_many

    (tmp_path / "snapshot").mkdir()
    (tmp_path / "manifest.json").write_text('{"commit":"abc"}')
    (tmp_path / "prompt.md").write_text("test")
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = TimeoutError()
            raise error
        return SimpleNamespace(id="gen-123", model="model", choices=[SimpleNamespace(
            message=SimpleNamespace(content="invalid", tool_calls=[]), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, cost=0.01))

    async def sleep(_):
        pass

    monkeypatch.setattr("reporeview_arena.orchestrator.asyncio.sleep", sleep)
    results = await audit_many(tmp_path, [{"id": "m", "model": "openrouter/test"}],
                               completion=completion, retries=1)
    assert results[0]["stage"] == "report"
    usage = read_usage(tmp_path)
    run = usage["runs"][0]
    assert len(run["attempts"]) == 2
    assert run["summary"]["cost_status"] == "partial"
    assert run["summary"]["unknown_cost_attempts"] == 1
    assert run["summary"]["openrouter_credits_usd"] == "0.01"
    assert run["summary"]["prompt_tokens"] == 20
    assert "invalid" not in json.dumps(usage)


def test_empty_workspace_has_no_fabricated_cost(tmp_path: Path):
    assert read_usage(tmp_path)["runs"] == []


def test_legacy_attempt_without_cache_fields_remains_readable(tmp_path: Path):
    run_id = "a" * 32
    attempt_dir = tmp_path / "usage" / run_id / "old-model"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "001-01.json").write_text(json.dumps({
        "run_id": run_id, "model_id": "old-model", "started_at": "2026-01-01T00:00:00+00:00",
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        "cost": {"openrouter_credits_usd": "0.02"},
    }), encoding="utf-8")

    run = read_usage(tmp_path, run_id)["runs"][0]

    assert run["summary"]["openrouter_credits_usd"] == "0.02"
    assert run["summary"]["cache_discount_usd"] is None
    assert run["summary"]["cache_discount_status"] == "unavailable"
    assert run["summary"]["unknown_cache_discount_attempts"] == 1


@pytest.mark.asyncio
async def test_usage_write_failure_stops_further_calls(tmp_path, monkeypatch):
    from reporeview_arena.orchestrator import audit_many

    (tmp_path / "snapshot").mkdir()
    (tmp_path / "prompt.md").write_text("test")
    (tmp_path / "manifest.json").write_text('{"commit":"abc"}')
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(usage=None)

    def cannot_save(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("reporeview_arena.orchestrator.finish_attempt", cannot_save)
    result, = await audit_many(tmp_path, [{"id": "m", "model": "openrouter/test"}], completion=completion)
    assert calls == 1
    assert result["stage"] == "usage"
    assert read_usage(tmp_path)["runs"][0]["attempts"][0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_provider_failure_after_success_preserves_observed_usage(tmp_path):
    from reporeview_arena.orchestrator import audit_many

    (tmp_path / "snapshot").mkdir()
    (tmp_path / "prompt.md").write_text("PRIVATE_PROMPT")
    (tmp_path / "manifest.json").write_text('{"commit":"abc"}')
    progress = []
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("PRIVATE_EXCEPTION")
        tool = SimpleNamespace(id="call-1", function=SimpleNamespace(name="list_files", arguments="{}"))
        return SimpleNamespace(id="gen-123", choices=[SimpleNamespace(
            message=SimpleNamespace(content="PRIVATE_RESPONSE", tool_calls=[tool]))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2, cost=0.001))

    result, = await audit_many(tmp_path, [{"id": "m", "model": "openrouter/test"}],
                               completion=completion, retries=0, progress=progress.append)
    assert result["stage"] == "provider"
    assert result["usage_summary"]["cost_status"] == "partial"
    assert result["usage_summary"]["openrouter_credits_usd"] == "0.001"
    assert any(update.get("run_id") for update in progress)
    stored = json.dumps(read_usage(tmp_path))
    assert "PRIVATE_PROMPT" not in stored
    assert "PRIVATE_RESPONSE" not in stored
    assert "PRIVATE_EXCEPTION" not in stored


@pytest.mark.asyncio
async def test_failed_attempts_record_error_class_and_http_status(tmp_path, monkeypatch):
    from reporeview_arena.orchestrator import audit_many

    (tmp_path / "snapshot").mkdir()
    (tmp_path / "prompt.md").write_text("test")
    (tmp_path / "manifest.json").write_text('{"commit":"abc"}')

    class RateLimitError(Exception):
        status_code = 429

    class NotFoundError(Exception):
        status_code = 404

    errors = [RateLimitError("PRIVATE_EXCEPTION Authorization: Bearer sk-secret"),
              NotFoundError("PRIVATE_EXCEPTION no endpoints")]

    async def completion(**kwargs):
        raise errors.pop(0)

    async def sleep(_):
        pass

    monkeypatch.setattr("reporeview_arena.orchestrator.asyncio.sleep", sleep)
    result, = await audit_many(tmp_path, [{"id": "m", "model": "openrouter/test"}],
                               completion=completion, retries=1)

    assert result["stage"] == "provider"
    assert result["http_status"] == 404
    attempts = read_usage(tmp_path)["runs"][0]["attempts"]
    assert [(a["status"], a["error_type"], a["http_status"]) for a in attempts] == [
        ("failed", "RateLimitError", 429), ("failed", "NotFoundError", 404)]
    stored = json.dumps(read_usage(tmp_path))
    assert "PRIVATE_EXCEPTION" not in stored
    assert "sk-secret" not in stored


def test_attempt_error_fields_accept_only_safe_values(tmp_path):
    from reporeview_arena.usage import finish_attempt, new_run_id, start_attempt

    run_id = new_run_id()
    path, record = start_attempt(tmp_path, run_id, "m", "openrouter/test", 1, 1)
    assert record["error_type"] is None and record["http_status"] is None
    finish_attempt(path, record, failure={"error_type": "Bad <type> PRIVATE_DETAIL",
                                          "http_status": 999, "error": "PRIVATE_DETAIL"})
    saved = json.loads(path.read_text())
    assert saved["status"] == "failed"
    assert saved["error_type"] is None and saved["http_status"] is None
    assert "PRIVATE_DETAIL" not in path.read_text()

    path, record = start_attempt(tmp_path, run_id, "m", "openrouter/test", 2, 1)
    finish_attempt(path, record, failure={"error_type": "TimeoutError", "http_status": True})
    saved = json.loads(path.read_text())
    assert saved["error_type"] == "TimeoutError" and saved["http_status"] is None

    path, record = start_attempt(tmp_path, run_id, "m", "openrouter/test", 3, 1)
    finish_attempt(path, record, response={"usage": {"cost": 0}})
    saved = json.loads(path.read_text())
    assert saved["status"] == "response_received"
    assert saved["error_type"] is None and saved["http_status"] is None


def test_cli_usage_lists_history_and_filters_run(tmp_path, capsys):
    from reporeview_arena.cli import execute, parser
    from reporeview_arena.usage import finish_attempt, new_run_id, start_attempt

    (tmp_path / "manifest.json").write_text('{"commit":"abc"}')
    run_id = new_run_id()
    path, record = start_attempt(tmp_path, run_id, "m", "openrouter/test", 1, 1)
    finish_attempt(path, record, response={"usage": {"cost": 0}})
    execute(parser().parse_args(["usage", "--workspace", str(tmp_path), "--run-id", run_id]))
    result = json.loads(capsys.readouterr().out)
    assert result["runs"][0]["summary"]["cost_status"] == "complete"
    assert result["runs"][0]["summary"]["openrouter_credits_usd"] == "0"
    assert result["runs"][0]["summary"]["cache_discount_status"] == "unavailable"


@pytest.mark.asyncio
async def test_concurrent_models_and_repeated_runs_keep_separate_attempts(tmp_path):
    from reporeview_arena.orchestrator import audit_many

    (tmp_path / "snapshot").mkdir()
    (tmp_path / "prompt.md").write_text("test")
    (tmp_path / "manifest.json").write_text('{"commit":"abc"}')
    started = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def completion(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await release.wait()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="bad", tool_calls=[]),
                                                         finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, cost=0.001))

    models = [{"id": "a", "model": "openrouter/a"}, {"id": "b", "model": "openrouter/b"}]
    running = asyncio.create_task(audit_many(tmp_path, models, completion=completion))
    await asyncio.wait_for(started.wait(), 2)
    partial = read_usage(tmp_path)["runs"][0]
    assert len(partial["attempts"]) == 2
    assert partial["summary"]["cost_status"] == "unavailable"
    release.set()
    await running
    await audit_many(tmp_path, models, completion=completion)
    runs = read_usage(tmp_path)["runs"]
    assert len(runs) == 2
    assert all(run["summary"]["openrouter_credits_usd"] == "0.002" for run in runs)
    assert all(set(run["models"]) == {"a", "b"} for run in runs)
