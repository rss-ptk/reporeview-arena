from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from reporeview_arena.modes import (
    DEFAULT_REVIEW_MODES,
    normalize_review_modes,
    rubric_fields_for_modes,
)
from reporeview_arena.orchestrator import (
    _execute_tool,
    audit_many,
    audit_one,
    model_slug,
    provider_preferences,
)
from reporeview_arena.prompts import build_prompt
from reporeview_arena.reports import parse_report, validate_locations
from reporeview_arena.repository import RepositoryReader, snapshot_repo
from reporeview_arena.scoring import calculate, leaderboard, leaderboards
from reporeview_arena.storage import load_json, save_json


def git(root: Path, *cmd: str):
    return subprocess.run(["git", "-C", str(root), *cmd], check=True, capture_output=True)


@pytest.fixture
def competition(tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "main.py").write_text("def run():\n    return 123\n")
    (repo / ".env").write_text("PASSWORD=SECRET")
    (repo / "private.pem").write_text("private credential")
    (repo / "README.md").write_text("# demo")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    workspace = tmp_path / "arena"
    manifest = snapshot_repo(repo, workspace)
    save_json(workspace / "manifest.json", manifest)
    reader = RepositoryReader(workspace / "snapshot")
    (workspace / "prompt.md").write_text(
        build_prompt(manifest, "find bugs", reader.describe()), encoding="utf-8"
    )
    return repo, workspace, manifest


def sample_report():
    return {
        "summary": "A bug", "hotpath": "main.py → return",
        "findings": [
            {"id": "F-1", "title": "Incorrect balance", "severity": "P1", "file": "main.py",
             "line": 2, "description": "Problem", "evidence": "return 123",
             "recommendation": "change logic", "confidence": 0.9,
             "evidence_status": "strong_evidence"},
            {"id": "F-2", "title": "Same bug repeated", "severity": "P1", "file": "main.py",
             "line": 2, "description": "Problem", "evidence": "return 123",
             "recommendation": "change logic", "confidence": 0.9,
             "evidence_status": "strong_evidence"},
        ],
        "golden_feature": {"name": "Better counting", "mechanism": "less errors"},
        "improvements": [{"title": "Useful improvement", "impact": "faster"}],
    }


def test_snapshot_denies_credentials_and_is_frozen(competition):
    repo, workspace, manifest = competition
    assert manifest["file_count"] == 2
    assert manifest["commit"] == git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    assert not (workspace / "snapshot" / ".env").exists()
    assert not (workspace / "snapshot" / "private.pem").exists()
    assert "SECRET" not in (workspace / "prompt.md").read_text()
    assert "find bugs" in (workspace / "prompt.md").read_text()
    prompt = (workspace / "prompt.md").read_text()
    assert "## Code audit" in prompt
    assert "## Golden Feature" in prompt
    assert (workspace / "snapshot" / "main.py").read_text() == "def run():\n    return 123\n"
    (repo / "main.py").write_text("modified")
    assert "123" in (workspace / "snapshot" / "main.py").read_text()
    with pytest.raises(ValueError, match="dirty"):
        snapshot_repo(repo, workspace.parent / "another")


def test_reader_is_bounded_and_prevents_traversal(competition):
    _, workspace, _ = competition
    r = RepositoryReader(workspace / "snapshot")
    assert "main.py" in r.list_files()
    assert "2:     return 123" in r.read_file("main.py")
    assert "ERROR" in r.read_file("../project/.env")
    assert "main.py:2" in r.search_code("return 123")
    assert "ERROR" in r.search_code("")
    assert "ERROR" in _execute_tool(r, "fake", {})


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_snapshot_preserves_source_newlines(competition, newline):
    repo, workspace, _ = competition
    source = newline.join([b"def run():", b"    return 123", b""])
    (repo / "main.py").write_bytes(source)
    git(repo, "add", "main.py")
    git(repo, "commit", "--allow-empty", "-qm", "set source newlines")
    destination = workspace.parent / "newline-arena"

    snapshot_repo(repo, destination)

    assert (destination / "snapshot" / "main.py").read_bytes() == source
    reader = RepositoryReader(destination / "snapshot")
    assert reader.read_file("main.py") == "1: def run():\n2:     return 123"


def test_reports_and_scorecard(competition):
    _, workspace, _ = competition
    report = parse_report("```json\n" + json.dumps(sample_report()) + "\n```", {"main.py"})
    with pytest.raises(ValueError, match="outside snapshot"):
        bad = sample_report()
        bad["findings"][0]["file"] = "secret.txt"
        parse_report(json.dumps(bad), {"main.py"})
    validate_locations(report, workspace / "snapshot")
    badline = sample_report()
    badline["findings"][0]["line"] = 999
    with pytest.raises(ValueError, match="only"):
        validate_locations(parse_report(json.dumps(badline), {"main.py"}), workspace / "snapshot")
    judgments = {"findings": {
        "F-1": {"verdict": "confirmed", "issue_key": "SAME", "severity": "P0"},
        "F-2": {"verdict": "confirmed", "issue_key": "SAME", "severity": "P1"},
    }, "rubric": {"improvements": 12, "gold": 15, "rigor": 4}}
    result = calculate(report, judgments)
    assert result["bugs"] == 20
    assert result["improvements"] == 12
    assert result["gold"] == 15
    assert "rigor" not in result
    assert result["total"] == 47
    assert result["confirmed"] == 2
    judgment2 = {"findings": {"F-1": {"verdict": "false_positive"}}}
    assert calculate(report, judgment2)["total"] == 0
    assert leaderboard({"alpha": report, "beta": report}, {"alpha": judgments})[0]["model"] == "alpha"
    save_json(workspace / "reports" / "alpha.json", report)
    assert load_json(workspace / "reports" / "alpha.json")["findings"][0]["id"] == "F-1"


def test_review_modes_default_to_both_and_reject_empty_or_unknown_modes():
    assert normalize_review_modes(None) == list(DEFAULT_REVIEW_MODES)
    assert normalize_review_modes(["golden_feature", "code_audit"]) == [
        "code_audit", "golden_feature",
    ]
    with pytest.raises(ValueError, match="at least one"):
        normalize_review_modes([])
    with pytest.raises(ValueError, match="unknown"):
        normalize_review_modes(["code_audit", "other"])


def test_rubric_fields_only_include_improvements_and_golden_feature():
    assert rubric_fields_for_modes(["code_audit"]) == ("improvements",)
    assert rubric_fields_for_modes(["golden_feature"]) == ("gold",)
    assert rubric_fields_for_modes(["code_audit", "golden_feature"]) == (
        "improvements", "gold",
    )


def test_report_schema_requires_only_selected_deliverables():
    legacy = parse_report(json.dumps(sample_report()), {"main.py"})
    assert len(legacy["findings"]) == 2
    assert legacy["golden_feature"]["name"] == "Better counting"

    audit = parse_report(
        json.dumps({"summary": "Audit", "findings": []}),
        {"main.py"}, review_modes=["code_audit"],
    )
    assert audit["findings"] == []
    assert "golden_feature" not in audit

    feature = parse_report(
        json.dumps({"summary": "Proposal", "golden_feature": {"name": "Feature"}}),
        {"main.py"}, review_modes=["golden_feature"],
    )
    assert feature["findings"] == []
    assert feature["golden_feature"]["name"] == "Feature"

    with pytest.raises(ValueError, match="findings array"):
        parse_report(
            json.dumps({"golden_feature": {"name": "Feature"}}),
            {"main.py"}, review_modes=["code_audit", "golden_feature"],
        )
    with pytest.raises(ValueError, match="golden_feature"):
        parse_report(json.dumps({"findings": []}), {"main.py"})


def test_mode_specific_leaderboards_score_audit_and_feature_separately():
    report = sample_report()
    judgments = {
        "alpha": {
            "findings": {
                "F-1": {"verdict": "confirmed", "issue_key": "SAME", "severity": "P0"},
                "F-2": {"verdict": "confirmed", "issue_key": "SAME", "severity": "P1"},
            },
            "rubric": {"improvements": 20, "gold": 1, "rigor": 4},
        },
        "beta": {"rubric": {"gold": 18}},
    }
    boards = leaderboards(
        {"alpha": report, "beta": report}, judgments,
        ["code_audit", "golden_feature"],
    )
    assert boards["code_audit"][0]["model"] == "alpha"
    assert boards["code_audit"][0]["total"] == 40
    assert "rigor" not in boards["code_audit"][0]
    assert boards["golden_feature"][0]["model"] == "beta"
    assert boards["golden_feature"][0]["total"] == 18

    maximum_report = sample_report()
    finding_template = maximum_report["findings"][0]
    maximum_report["findings"] = [
        {**finding_template, "id": f"F-{index}"} for index in range(4)
    ]
    maximum_judgment = {
        "findings": {
            f"F-{index}": {
                "verdict": "confirmed", "issue_key": f"ISSUE-{index}",
                "severity": "P0",
            }
            for index in range(4)
        },
        "rubric": {"improvements": 20, "gold": 20, "rigor": 5},
    }
    maximum_score = calculate(maximum_report, maximum_judgment)
    assert maximum_score["total"] == 100
    maximum_boards = leaderboards(
        {"maximum": maximum_report}, {"maximum": maximum_judgment},
        ["code_audit", "golden_feature"],
    )
    assert maximum_boards["code_audit"][0]["total"] == 80
    assert maximum_boards["golden_feature"][0]["total"] == 20


def test_reject_invalid_ids_and_json():
    with pytest.raises(ValueError):
        model_slug("../malicious")
    with pytest.raises(ValueError, match="unique"):
        bad = sample_report()
        bad["findings"][1]["id"] = "F-1"
        parse_report(json.dumps(bad), {"main.py"})
    with pytest.raises(ValueError, match="positive"):
        bad = sample_report()
        bad["findings"][0]["line"] = -1
        parse_report(json.dumps(bad), {"main.py"})


@pytest.mark.asyncio
async def test_model_audit_toolloop_offline_mock(competition):
    _, workspace, _ = competition
    calls = []
    tool = SimpleNamespace(id="t1", function=SimpleNamespace(name="read_file", arguments='{"path":"main.py"}'))

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            msg = SimpleNamespace(content="", tool_calls=[tool])
        else:
            msg = SimpleNamespace(content=json.dumps(sample_report()), tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))

    result = await audit_one(workspace, {"id": "mock", "model": "test/mock"}, completion=completion)
    assert result["findings"] == 2
    assert calls[0]["tools"]
    assert calls[1]["messages"][-1]["role"] == "tool"
    assert calls[1]["messages"][-1]["content"].startswith("1: def run")
    output = load_json(workspace / "reports" / "mock.json")
    assert output["audit_metadata"]["commit"] == load_json(workspace / "manifest.json")["commit"]
    assert output["audit_metadata"]["calls"] == 2


@pytest.mark.asyncio
async def test_openrouter_session_id_is_stable_across_retries_and_rounds(competition):
    _, workspace, _ = competition
    from reporeview_arena.usage import new_run_id, read_usage

    run_id = new_run_id()
    calls = []
    tool = SimpleNamespace(id="t1", function=SimpleNamespace(
        name="read_file", arguments='{"path":"main.py"}'))

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError()
        message = (SimpleNamespace(content="", tool_calls=[tool]) if len(calls) == 2 else
                   SimpleNamespace(content=json.dumps(sample_report()), tool_calls=[]))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    await audit_one(
        workspace, {"id": "mock", "model": "openrouter/vendor/model"},
        max_steps=1, retries=1, retry_delay=0, completion=completion, run_id=run_id,
    )

    expected = f"reporeview-{run_id}-mock"
    assert [call["extra_body"]["session_id"] for call in calls] == [expected] * 3
    attempts = read_usage(workspace, run_id)["runs"][0]["attempts"]
    assert [item["session_id"] for item in attempts] == [expected] * 3


@pytest.mark.asyncio
async def test_openrouter_session_id_isolated_by_model_and_execution(competition):
    _, workspace, _ = competition
    from reporeview_arena.usage import read_usage

    async def completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(sample_report()), tool_calls=[]))], usage=None)

    models = [{"id": "first", "model": "openrouter/vendor/one"},
              {"id": "second", "model": "openrouter/vendor/two"}]
    first_run = await audit_many(workspace, models, completion=completion, concurrency=2)
    first_records = read_usage(workspace, first_run[0]["run_id"])["runs"][0]["attempts"]
    first_by_model = {item["model_id"]: item["session_id"] for item in first_records}
    assert first_by_model["first"] != first_by_model["second"]

    second_run = await audit_many(workspace, models, completion=completion, concurrency=2)
    second_records = read_usage(workspace, second_run[0]["run_id"])["runs"][0]["attempts"]
    second_by_model = {item["model_id"]: item["session_id"] for item in second_records}
    assert first_by_model.keys() == second_by_model.keys()
    assert set(first_by_model.values()).isdisjoint(second_by_model.values())


@pytest.mark.asyncio
async def test_openrouter_session_id_is_not_sent_to_unverified_endpoint(competition):
    _, workspace, _ = competition
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(sample_report()), tool_calls=[]))], usage=None)

    await audit_one(
        workspace, {"id": "proxy", "model": "openrouter/vendor/model",
                    "api_base": "https://proxy.example/v1"},
        completion=completion,
    )

    assert "extra_body" not in calls[0]


@pytest.mark.asyncio
async def test_openrouter_provider_preferences_sent_with_session_on_every_call(competition):
    _, workspace, _ = competition
    calls = []
    tool = SimpleNamespace(id="t1", function=SimpleNamespace(
        name="read_file", arguments='{"path":"main.py"}'))

    async def completion(**kwargs):
        calls.append(kwargs)
        message = (SimpleNamespace(content="", tool_calls=[tool]) if len(calls) == 1 else
                   SimpleNamespace(content=json.dumps(sample_report()), tool_calls=[]))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    provider = {"sort": "price", "quantizations": ["fp8", "bf16"],
                "max_price": {"prompt": 0.5, "completion": 2}}
    await audit_one(
        workspace, {"id": "mock", "model": "openrouter/vendor/model", "provider": provider},
        max_steps=1, completion=completion,
    )

    assert len(calls) == 2
    assert [call["extra_body"]["provider"] for call in calls] == [provider] * 2
    assert all("order" not in call["extra_body"]["provider"] for call in calls)
    assert len({call["extra_body"]["session_id"] for call in calls}) == 1


@pytest.mark.parametrize("model", [
    {"model": "openrouter/vendor/model", "provider": {"order": ["a", "b"]}},
    {"model": "openrouter/vendor/model", "provider": {"sort": "price", "order": ["a"]}},
    {"model": "openrouter/vendor/model", "provider": {"only": ["a"]}},
    {"model": "openrouter/vendor/model", "provider": {}},
    {"model": "openrouter/vendor/model", "provider": ["price"]},
    {"model": "openrouter/vendor/model", "provider": {"sort": "cheapest"}},
    {"model": "openrouter/vendor/model", "provider": {"sort": ["price"]}},
    {"model": "openrouter/vendor/model", "provider": {"quantizations": []}},
    {"model": "openrouter/vendor/model", "provider": {"quantizations": "fp8"}},
    {"model": "openrouter/vendor/model", "provider": {"quantizations": ["int3"]}},
    {"model": "openrouter/vendor/model", "provider": {"quantizations": ["fp8", "fp8"]}},
    {"model": "openrouter/vendor/model", "provider": {"max_price": {}}},
    {"model": "openrouter/vendor/model", "provider": {"max_price": {"prompt": -1}}},
    {"model": "openrouter/vendor/model", "provider": {"max_price": {"prompt": True}}},
    {"model": "openrouter/vendor/model", "provider": {"max_price": {"prompt": float("inf")}}},
    {"model": "openrouter/vendor/model", "provider": {"max_price": {"tokens": 1}}},
    {"model": "openai/model", "provider": {"sort": "price"}},
    {"model": "openrouter/vendor/model", "api_base": "https://proxy.example/v1",
     "provider": {"sort": "price"}},
])
@pytest.mark.asyncio
async def test_invalid_provider_preferences_rejected_before_any_call(competition, model):
    _, workspace, _ = competition
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)

    with pytest.raises(ValueError):
        await audit_one(workspace, {"id": "mock", **model}, completion=completion)
    assert not calls
    assert not (workspace / "usage").exists()


def test_provider_preferences_are_copied_and_order_explained():
    source = {"sort": "throughput", "quantizations": ["fp16"], "max_price": {"completion": 3}}
    model = {"model": "openrouter/vendor/model", "provider": source}
    result = provider_preferences(model)
    assert result == source
    result["quantizations"].append("int4")
    assert source["quantizations"] == ["fp16"]
    assert provider_preferences({"model": "openai/model"}) is None
    with pytest.raises(ValueError, match="sticky routing"):
        provider_preferences({"model": "openrouter/x/y", "provider": {"order": ["a"]}})


@pytest.mark.asyncio
async def test_tool_round_budget_keeps_request_prefix_stable(competition):
    _, workspace, _ = competition
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            tool = SimpleNamespace(id=f"t{len(calls)}", function=SimpleNamespace(
                name="read_file", arguments='{"path":"main.py"}'))
            message = SimpleNamespace(content="", tool_calls=[tool])
        else:
            message = SimpleNamespace(content=json.dumps(sample_report()), tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    await audit_one(workspace, {"id": "mock", "model": "test/mock"},
                    max_steps=2, completion=completion)

    # System message is identical on every call (prompt cache + sticky routing key).
    systems = {call["messages"][0]["content"] for call in calls}
    assert len(systems) == 1
    system = systems.pop()
    assert "budget of 2 tool rounds" in system and "6 tool calls" in system
    assert "remaining" not in system
    # History is append-only: each request is an exact prefix of the next.
    for before, after in zip(calls, calls[1:]):
        assert after["messages"][:len(before["messages"])] == before["messages"]
    # The counter rides on the last tool result of each round.
    assert calls[1]["messages"][-1]["role"] == "tool"
    assert calls[1]["messages"][-1]["content"].endswith("[arena] 1 of 2 tool rounds remaining.")
    assert calls[2]["messages"][-2]["content"].endswith("[arena] 0 of 2 tool rounds remaining.")
    assert "tools" in calls[0] and "tools" in calls[1]
    assert "tools" not in calls[2]
    assert "No more tools" in calls[2]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_model_audit_accepts_golden_feature_only_report(competition):
    _, workspace, manifest = competition
    manifest["review_modes"] = ["golden_feature"]
    save_json(workspace / "manifest.json", manifest)
    reader = RepositoryReader(workspace / "snapshot")
    (workspace / "prompt.md").write_text(
        build_prompt(manifest, "", reader.describe()), encoding="utf-8",
    )
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(
            content=json.dumps({"summary": "Proposal", "golden_feature": {"name": "Feature"}}),
            tool_calls=[],
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    result = await audit_one(
        workspace, {"id": "feature", "model": "test/feature"}, completion=completion,
    )
    report = load_json(workspace / "reports" / "feature.json")
    assert len(calls) == 1
    assert result["findings"] == 0
    assert report["findings"] == []
    assert report["golden_feature"]["name"] == "Feature"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,error_type", [
    ("not-json", "JSONDecodeError"),
    ("[]", "ValueError"),
    ("{}", "KeyError"),
    ('{"path":"main.py","start_line":null}', "TypeError"),
])
async def test_model_audit_recovers_from_invalid_tool_arguments(competition, arguments, error_type):
    _, workspace, _ = competition
    calls = []
    tool = SimpleNamespace(
        id="invalid", function=SimpleNamespace(name="read_file", arguments=arguments)
    )

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            message = SimpleNamespace(content="", tool_calls=[tool])
        else:
            message = SimpleNamespace(content=json.dumps(sample_report()), tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    result = await audit_one(workspace, {"id": "mock", "model": "test/mock"}, completion=completion)

    assert result["findings"] == 2
    tool_reply = calls[1]["messages"][-1]
    assert (tool_reply["role"], tool_reply["tool_call_id"]) == ("tool", "invalid")
    assert tool_reply["content"] == (f"ERROR: {error_type}: invalid tool arguments"
                                     "\n\n[arena] 34 of 35 tool rounds remaining.")


@pytest.mark.asyncio
async def test_independent_models_and_errors(competition):
    _, workspace, _ = competition

    async def completion(**kwargs):
        if kwargs["model"] == "err":
            raise ConnectionError("redacted secret not to leak")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(sample_report()), tool_calls=[]))], usage=None)

    results = await audit_many(workspace, [{"id": "ok", "model": "ok"},
                                           {"id": "broken", "model": "err"}], completion=completion)
    assert results[0]["findings"] == 2
    assert results[1]["error_type"] == "ConnectionError"
    assert "secret" not in json.dumps(results)
