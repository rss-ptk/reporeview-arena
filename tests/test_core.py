from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from reporeview_arena.orchestrator import _execute_tool, audit_many, audit_one, model_slug
from reporeview_arena.prompts import build_prompt
from reporeview_arena.reports import parse_report, validate_locations
from reporeview_arena.repository import RepositoryReader, snapshot_repo
from reporeview_arena.scoring import calculate, leaderboard
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
    (workspace / "prompt.md").write_text(build_prompt(manifest, "find bugs", reader.describe()))
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
    assert result["total"] == 51
    assert result["confirmed"] == 2
    judgment2 = {"findings": {"F-1": {"verdict": "false_positive"}}}
    assert calculate(report, judgment2)["total"] == 0
    assert leaderboard({"alpha": report, "beta": report}, {"alpha": judgments})[0]["model"] == "alpha"
    save_json(workspace / "reports" / "alpha.json", report)
    assert load_json(workspace / "reports" / "alpha.json")["findings"][0]["id"] == "F-1"


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
