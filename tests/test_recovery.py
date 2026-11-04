from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from reporeview_arena.orchestrator import audit_many, audit_one

MODEL = {"id": "mock", "model": "test/mock"}
SECRET = "PRIVATE_PROVIDER_CREDENTIAL_AND_REQUEST_BODY"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "arena"
    (root / "snapshot").mkdir(parents=True)
    (root / "snapshot" / "main.py").write_text("def run():\n    return 123\n", encoding="utf-8")
    (root / "prompt.md").write_text("Audit main.py and return a JSON report.", encoding="utf-8")
    (root / "manifest.json").write_text('{"commit": "abc"}', encoding="utf-8")
    return root


@pytest.fixture
def delays(monkeypatch) -> list[float]:
    recorded = []

    async def sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr("reporeview_arena.orchestrator.asyncio.sleep", sleep)
    return recorded


def report(**location) -> str:
    finding = {
        "title": "Incorrect result", "severity": "P1", "file": "main.py", "line": 2,
        "description": "Returns a constant", "evidence": "return 123",
        "recommendation": "Return the calculated result",
    }
    finding.update(location)
    return json.dumps({"findings": [finding], "golden_feature": {"name": "Calculate results"}})


def reply(content=None, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=report() if content is None else content, tool_calls=[]),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


class ProviderFailure(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(SECRET)
        self.status_code = status
        headers = {"Authorization": SECRET}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        self.response = SimpleNamespace(status_code=status, headers=headers, text=SECRET)
        self.body = {"messages": SECRET}


class CompletionScript:
    def __init__(self, *events):
        self.events = events
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        event = self.events[min(len(self.calls) - 1, len(self.events) - 1)]
        if isinstance(event, Exception):
            raise event
        return event


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_transient_provider_failures_retry_with_bounded_exponential_backoff(workspace, delays, status):
    completion = CompletionScript(ProviderFailure(status), ProviderFailure(status), reply())

    result = await audit_one(workspace, MODEL, completion=completion, retries=2, retry_delay=2)

    assert result["findings"] == 1
    assert len(completion.calls) == 3
    assert delays == [2, 4]
    assert all(call["num_retries"] == 0 for call in completion.calls)
    assert all(call["messages"] == completion.calls[0]["messages"] for call in completion.calls)
    assert (workspace / "reports" / "mock.json").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["7", "date"])
async def test_retry_after_is_honored(workspace, delays, header):
    if header == "date":
        header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=40), usegmt=True)
    completion = CompletionScript(ProviderFailure(429, header), reply())

    result = await audit_one(workspace, MODEL, completion=completion, retries=1, retry_delay=1)

    assert result["findings"] == 1
    assert len(completion.calls) == 2
    assert len(delays) == 1
    if header == "7":
        assert delays[0] == 7
    else:
        assert 35 <= delays[0] <= 40


@pytest.mark.asyncio
async def test_long_retry_after_stops_automatic_retry(workspace, delays):
    completion = CompletionScript(ProviderFailure(429, "120"), reply())

    result, = await audit_many(workspace, [MODEL], completion=completion, retries=2, retry_delay=1)

    assert len(completion.calls) == 1
    assert delays == []
    assert result["error_type"] == "ProviderFailure"
    assert result["stage"] == "provider"
    assert result["http_status"] == 429
    assert result["retry_after_seconds"] == 120
    assert "rate limit" in result["error"].lower()


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_preserves_original_error_type(workspace, delays):
    completion = CompletionScript(ProviderFailure(503))

    result, = await audit_many(workspace, [MODEL], completion=completion, retries=2, retry_delay=0.5)

    assert len(completion.calls) == 3
    assert delays == [0.5, 1]
    assert result["error_type"] == "ProviderFailure"
    assert result["stage"] == "provider"
    assert result["http_status"] == 503
    assert "unavailable" in result["error"].lower()
    assert not (workspace / "reports" / "mock.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(400, "request"), (401, "authentication")])
async def test_nonretryable_provider_errors_have_safe_diagnostics(workspace, delays, capsys, status, reason):
    completion = CompletionScript(ProviderFailure(status, "11"))

    result, = await audit_many(workspace, [MODEL], completion=completion)

    assert len(completion.calls) == 1
    assert delays == []
    assert result["error_type"] == "ProviderFailure"
    assert result["stage"] == "provider"
    assert result["http_status"] == status
    assert result["retry_after_seconds"] == 11
    assert reason in result["error"].lower()
    printed = capsys.readouterr()
    assert result["error"] in printed.out
    assert SECRET not in printed.out + printed.err
    assert SECRET not in json.dumps(result)
    assert not (workspace / "failures" / "mock.json").exists()
    assert all(SECRET not in path.read_text(encoding="utf-8") for path in workspace.rglob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("content,finish_reason,reason", [
    ('{"raw":"LOCAL_REPORT_PAYLOAD"', "stop", "json"),
    ('{"findings":[],"summary":"LOCAL_REPORT_PAYLOAD"}', "stop", "schema"),
    ("", "stop", "empty"),
    ('{"findings": [', "length", "truncat"),
    (report(file="../LOCAL_REPORT_PAYLOAD.py"), "stop", "file"),
    (report(line=999), "stop", "line"),
])
async def test_invalid_reports_are_saved_locally_without_repair_or_relaxed_validation(
    workspace, delays, capsys, content, finish_reason, reason,
):
    completion = CompletionScript(reply(content, finish_reason))

    result, = await audit_many(workspace, [MODEL], completion=completion)

    assert len(completion.calls) == 1
    assert delays == []
    assert result["error_type"] == "ValueError"
    assert result["stage"] == "report"
    assert reason in result["error"].lower()
    assert result["failure_file"] == "failures/mock.json"
    assert not (workspace / "reports" / "mock.json").exists()
    artifact = json.loads((workspace / result["failure_file"]).read_text(encoding="utf-8"))
    assert artifact["raw_response"] == content
    assert artifact["validation_error"]
    assert artifact["finish_reason"] == finish_reason
    assert artifact["usage"]["prompt_tokens"] == 3
    assert artifact["usage"]["completion_tokens"] == 2
    printed = capsys.readouterr()
    assert result["error"] in printed.out
    assert "LOCAL_REPORT_PAYLOAD" not in printed.out + printed.err + json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_tool_calls_after_budget_are_diagnosed_and_saved(workspace, structured):
    tool = SimpleNamespace(id="call-1", function=SimpleNamespace(
        name="read_file", arguments='{"path":"main.py"}'))
    first = reply("")
    first.choices[0].message.tool_calls = [tool]
    final = reply("" if structured else (
        "<tool_call><function=read_file><parameter=path>main.py</parameter>"
        "</function></tool_call>"
    ))
    if structured:
        final.choices[0].message.tool_calls = [tool]
    completion = CompletionScript(first, final)

    result, = await audit_many(workspace, [MODEL], completion=completion, max_steps=1)

    # Structured tool calls on the final call (tool_choice "none" ignored) get exactly
    # one more request without tools; text-form calls are not retried.
    assert len(completion.calls) == (3 if structured else 2)
    assert completion.calls[1]["tool_choice"] == "none"
    if structured:
        assert "tools" not in completion.calls[2]
    assert "tool" in result["error"].lower()
    assert "budget" in result["error"].lower()
    artifact = json.loads((workspace / result["failure_file"]).read_text(encoding="utf-8"))
    assert artifact["raw_response"] == final.choices[0].message.content
    if structured:
        assert artifact["tool_calls"][0]["function"]["arguments"] == '{"path":"main.py"}'
    assert not (workspace / "reports" / "mock.json").exists()


@pytest.mark.asyncio
async def test_partial_success_survives_provider_and_report_failures(workspace, delays):
    async def completion(**kwargs):
        if kwargs["model"] == "test/provider-fails":
            raise ProviderFailure(401)
        return reply("invalid JSON" if kwargs["model"] == "test/report-fails" else report())

    models = [MODEL, {"id": "provider", "model": "test/provider-fails"},
              {"id": "report", "model": "test/report-fails"}]
    results = await audit_many(workspace, models, completion=completion)

    assert [result["model"] for result in results] == ["mock", "provider", "report"]
    assert results[0]["findings"] == 1
    assert results[1]["stage"] == "provider"
    assert results[2]["stage"] == "report"
    assert (workspace / "reports" / "mock.json").is_file()
    assert not (workspace / "reports" / "provider.json").exists()
    assert not (workspace / "reports" / "report.json").exists()


def test_cli_forwards_retry_controls_and_reports_actual_saved_count(workspace, monkeypatch, capsys):
    from reporeview_arena.cli import execute, parser

    config = workspace / "models.json"
    config.write_text(json.dumps({"models": [MODEL]}), encoding="utf-8")
    observed = {}

    async def audit(workspace, models, **kwargs):
        observed.update(kwargs)
        return [{"model": "mock", "error": "Provider rate limit reached."}]

    monkeypatch.setattr("reporeview_arena.cli.audit_many", audit)
    args = parser().parse_args([
        "run", "--workspace", str(workspace), "--models", str(config),
        "--retries", "3", "--retry-delay", "2.5", "--confirm-send-code",
    ])

    with pytest.raises(RuntimeError, match="1 model audits failed; 0 reports saved"):
        execute(args)

    assert observed["retries"] == 3
    assert observed["retry_delay"] == 2.5
    assert "Provider rate limit reached." in capsys.readouterr().out


@pytest.mark.parametrize("delay", ["-1", "61", "nan"])
def test_cli_rejects_invalid_retry_delay_before_provider_calls(workspace, monkeypatch, delay):
    from reporeview_arena.cli import execute, parser

    args = parser().parse_args([
        "run", "--workspace", str(workspace), "--models", "not-read.json",
        "--retry-delay", delay, "--confirm-send-code",
    ])
    with pytest.raises(ValueError, match="--retry-delay"):
        execute(args)
