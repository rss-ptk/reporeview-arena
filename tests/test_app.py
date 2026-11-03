from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from reporeview_arena import app
from reporeview_arena.storage import save_json
from reporeview_arena.workspace import (
    GOLDEN_FEATURE_ASPECTS,
    MAX_FOCUS_LENGTH,
    REVIEW_FOCUS_CRITERIA,
    REVIEW_FOCUS_INSTRUCTIONS,
    format_golden_feature_direction,
    format_review_focus,
)


def _request(base_url: str, path: str, *, token: str | None = None,
             payload: dict | None = None, origin: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Arena-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base_url + path, data=body, headers=headers)
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as response:
        return response.code, json.loads(response.read())


@pytest.fixture
def dashboard(tmp_path: Path):
    server = app.create_server(port=0, data_directory=tmp_path / "app-data")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base_url + "/", timeout=2) as response:
            page = response.read().decode()
        token = re.search(r"const token\s*=\s*([^;]+);", page).group(1)
        yield server, base_url, json.loads(token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _workspace(path: Path) -> Path:
    path.mkdir()
    (path / "snapshot").mkdir()
    (path / "snapshot" / "main.py").write_text("print('demo')\n", encoding="utf-8")
    (path / "reports").mkdir()
    (path / "judgments").mkdir()
    (path / "prompt.md").write_text("Review this repository.", encoding="utf-8")
    save_json(path / "manifest.json", {
        "repo_name": "demo", "commit": "abc123", "file_count": 1,
        "files": [{"path": "main.py"}], "skipped": [],
    })
    return path


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    (path / "main.py").write_text("print('demo')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "main.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "demo"], check=True)
    return path


def test_web_cli_accepts_optional_workspace_and_models():
    from reporeview_arena.cli import parser

    args = parser().parse_args(["web", "--models", "models.json"])
    assert args.workspace is None
    assert args.models == Path("models.json")

    legacy = parser().parse_args(["web", "--workspace", "arena"])
    assert legacy.workspace == Path("arena")
    assert legacy.models is None

    init = parser().parse_args([
        "init", "--repo", "repo", "--out", "arena", "--focus", "Review risks",
    ])
    assert init.focus == "Review risks"


def test_web_cli_defaults_to_models_json_in_launch_directory(tmp_path, monkeypatch):
    from reporeview_arena import cli

    monkeypatch.chdir(tmp_path)
    configured = tmp_path / "models.json"
    configured.write_text(json.dumps({"models": [{"id": "configured"}]}), encoding="utf-8")
    received = []
    monkeypatch.setattr(
        cli, "serve",
        lambda workspace, *, port, models: received.append((workspace, port, models)),
    )

    cli.execute(cli.parser().parse_args(["web"]))
    assert received[-1] == (None, 8765, configured)

    configured.unlink()
    cli.execute(cli.parser().parse_args(["web"]))
    assert received[-1] == (None, 8765, None)

    cli.execute(cli.parser().parse_args(["web", "--models", "custom-models.json"]))
    assert received[-1] == (None, 8765, Path("custom-models.json"))


def test_repository_configuration_has_visible_help_and_review_history(dashboard):
    _, base_url, _ = dashboard
    with urlopen(base_url + "/", timeout=2) as response:
        page = response.read().decode()

    for field_id in ("repo-path",):
        assert f'aria-describedby="{field_id}-help"' in page
        assert f'id="{field_id}-help" role="tooltip"' in page
    assert 'id="save-repository-button"' in page
    assert 'id="review-history"' in page
    assert "repository_path" in page
    assert 'id="workspace-path"' not in page
    assert 'id="out-path"' not in page
    assert 'id="focus-custom-text"' in page
    assert 'id="golden-feature-aspects"' in page
    assert "Third-party integrations" in page
    assert "Integrações de terceiros" in page
    assert "The proposal should coherently address every selected aspect" in page
    assert "A proposta deve atender de forma coerente a todos os aspectos marcados" in page
    for aspect_id in GOLDEN_FEATURE_ASPECTS:
        assert f'id="golden-feature-aspect-{aspect_id}"' in page
        assert f'data-golden-feature-aspect value="{aspect_id}"' in page
    assert page.count('type="range"') == 9
    assert page.count('role="tooltip"') == 12
    assert 'maxlength="4000"' in page
    assert "Choose and save the root of a Git checkout" in page
    assert "Escolha e salve a raiz de um checkout Git" in page
    for criterion_id, (_label, default_value) in REVIEW_FOCUS_CRITERIA.items():
        assert f'id="priority-{criterion_id}"' in page
        assert f'value="{default_value}"' in page
        assert f'aria-describedby="priority-{criterion_id}-help' in page
    assert "Root folder of a clean local Git checkout." in page
    assert "Review focus" in page
    assert "Critical flows and impact" in page
    assert "Fluxos críticos e impacto" in page


def test_repository_setting_rejects_invalid_roots_and_persists_across_server_restart(
    dashboard, tmp_path,
):
    server, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    nested = repo / "nested"
    nested.mkdir()

    status, initial = _request(base_url, "/api/status")
    assert status == 200
    assert initial["repository_path"] is None

    status, _ = _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(nested)},
    )
    assert status == 400
    assert server.repository_path is None

    status, _ = _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    assert status == 200
    status, saved = _request(base_url, "/api/status")
    assert status == 200
    assert saved["repository_path"] == str(repo.resolve())

    restarted = app.create_server(port=0, data_directory=server.data_directory)
    restarted_thread = Thread(target=restarted.serve_forever, daemon=True)
    restarted_thread.start()
    restarted_url = f"http://127.0.0.1:{restarted.server_port}"
    try:
        status, persisted = _request(restarted_url, "/api/status")
        assert status == 200
        assert persisted["repository_path"] == str(repo.resolve())
    finally:
        restarted.shutdown()
        restarted.server_close()
        restarted_thread.join(timeout=2)


def test_folder_picker_marks_only_repository_roots_as_selectable(dashboard, tmp_path):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    nested = repo / "nested"
    nested.mkdir()

    status, root = _request(
        base_url, "/api/folders", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    assert status == 200
    assert root["is_git_repository"] is True

    status, parent = _request(
        base_url, "/api/folders", token=token, origin=base_url,
        payload={"path": str(tmp_path)},
    )
    assert status == 200
    project = next(item for item in parent["directories"] if item["name"] == "project")
    assert project["is_git_repository"] is True

    status, subdirectory = _request(
        base_url, "/api/folders", token=token, origin=base_url,
        payload={"path": str(nested)},
    )
    assert status == 200
    assert subdirectory["is_git_repository"] is False


def test_managed_reviews_are_unique_listed_by_repository_and_opened_by_id(dashboard, tmp_path):
    server, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    other_repo = _git_repo(tmp_path / "other-project")

    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    focus = {"criteria": {"critical_flows": 5}, "custom_text": "Check retry behavior"}
    status, first = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"review_focus": focus},
    )
    assert status == 201
    first_id = first["review_id"]
    first_path = server.data_directory / "reviews" / first_id
    assert first_path.is_dir()
    assert (first_path / "manifest.json").is_file()
    first_prompt = (first_path / "prompt.md").read_text(encoding="utf-8")
    assert "Check retry behavior" in first_prompt
    assert "## Golden feature direction" not in first_prompt

    status, second = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"review_focus": focus},
    )
    assert status == 201
    assert second["review_id"] != first_id

    status, history = _request(base_url, "/api/reviews")
    assert status == 200
    assert {review["id"] for review in history["reviews"]} == {
        first_id, second["review_id"],
    }

    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(other_repo)},
    )
    status, other_history = _request(base_url, "/api/reviews")
    assert status == 200
    assert other_history["reviews"] == []

    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    status, opened = _request(
        base_url, "/api/reviews/open", token=token, origin=base_url,
        payload={"id": first_id},
    )
    assert status == 200
    assert opened["manifest"]["repo_name"] == "project"
    status, active = _request(base_url, "/api/status")
    assert active["workspace"] == str(first_path.resolve())

    status, error = _request(
        base_url, "/api/reviews/open", token=token, origin=base_url,
        payload={"id": "../settings.json"},
    )
    assert status == 400
    assert error["error"]


def test_managed_review_prompt_separates_golden_feature_aspects_from_review_priorities(
    dashboard, tmp_path,
):
    server, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )

    status, created = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"review_focus": {
            "criteria": {"security": 5}, "custom_text": "",
            "golden_feature_aspects": [
                "architecture", "third_party_integrations",
            ],
        }},
    )

    assert status == 201
    prompt = (
        server.data_directory / "reviews" / created["review_id"] / "prompt.md"
    ).read_text(encoding="utf-8")
    priorities, direction = prompt.split("## Golden feature direction\n", 1)
    direction = direction.split("\n\n## Repository map", 1)[0]
    assert "Security and privacy — priority 5/5 (critical)" in priorities
    assert "Architecture and maintainability" not in priorities
    assert "Third-party integrations" not in priorities
    assert "Architecture and maintainability" in direction
    assert "Third-party integrations" in direction
    assert "exactly one" in prompt.lower()
    assert "state the mismatch" in direction


def test_reports_are_scoped_to_each_review_and_same_model_can_run_again(
    dashboard, tmp_path, monkeypatch,
):
    server, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    status, first = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"review_focus": {
            "criteria": {"critical_flows": 5}, "custom_text": "Review A",
        }},
    )
    assert status == 201
    first_id = first["review_id"]

    report = {
        "summary": "Review A report",
        "golden_feature": {"name": "Feature", "mechanism": ""},
        "findings": [],
    }
    status, imported = _request(
        base_url, "/api/import", token=token, origin=base_url,
        payload={"model": "mock", "content": json.dumps(report)},
    )
    assert status == 200
    assert imported["ok"] is True

    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [
        {"id": "mock", "model": "test/mock"},
    ]}), encoding="utf-8")
    server.models_path = config

    async def audit_many(path, models, **kwargs):
        return [{"model": model["id"], "findings": 0} for model in models]

    monkeypatch.setattr(app, "audit_many", audit_many)

    status, second = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"review_focus": {
            "criteria": {"security": 5}, "custom_text": "Review B",
        }},
    )
    assert status == 201
    second_id = second["review_id"]
    assert second_id != first_id
    second_path = server.data_directory / "reviews" / second_id
    assert "Review B" in (second_path / "prompt.md").read_text(encoding="utf-8")

    status, active = _request(base_url, "/api/status")
    assert status == 200
    assert active["review_id"] == second_id
    status, second_data = _request(base_url, "/api/data")
    assert status == 200
    assert second_data["reports"] == {}

    status, started = _request(
        base_url, "/api/run", token=token, origin=base_url,
        payload={"models": ["mock"], "confirm_send_code": True},
    )
    assert status == 202
    assert started["job"]["workspace"] == active["workspace"]

    status, opened = _request(
        base_url, "/api/reviews/open", token=token, origin=base_url,
        payload={"id": first_id},
    )
    assert status == 200
    assert opened["review_id"] == first_id
    status, first_data = _request(base_url, "/api/data")
    assert status == 200
    assert first_data["reports"]["mock"]["summary"] == "Review A report"

    status, history = _request(base_url, "/api/reviews")
    assert status == 200
    assert {review["id"] for review in history["reviews"]} == {first_id, second_id}


def test_startup_selects_the_latest_managed_review_for_the_saved_repository(
    dashboard, tmp_path,
):
    server, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    created = []
    for _ in range(2):
        status, review = _request(
            base_url, "/api/reviews", token=token, origin=base_url,
            payload={"review_focus": {"criteria": {}, "custom_text": ""}},
        )
        assert status == 201
        created.append(review)

    latest = max(created, key=lambda item: item["created_at"])
    restarted = app.create_server(port=0, data_directory=server.data_directory)
    try:
        assert restarted.active_review_id == latest["review_id"]
        assert restarted.active_workspace == (
            server.data_directory / "reviews" / latest["review_id"]
        ).resolve()
    finally:
        restarted.server_close()


def test_saved_repository_that_was_moved_is_reported_unavailable(tmp_path):
    data_directory = tmp_path / "app-data"
    moved_repository = tmp_path / "repository-that-is-not-here"
    save_json(data_directory / "settings.json", {
        "repository_path": str(moved_repository.resolve()),
    })
    server = app.create_server(port=0, data_directory=data_directory)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, configuration = _request(
            f"http://127.0.0.1:{server.server_port}", "/api/status",
        )
        assert status == 200
        assert configuration["repository_path"] == str(moved_repository.resolve())
        assert configuration["repository_available"] is False
        assert configuration["repository_error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_managed_review_creation_rejects_dirty_repository(dashboard, tmp_path):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    status, error = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"out": str(tmp_path / "chosen-by-browser")},
    )
    assert status == 400
    assert "review_focus" in error["error"]
    assert not (tmp_path / "chosen-by-browser").exists()

    status, first = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={"review_focus": {"criteria": {}, "custom_text": "Existing review"}},
    )
    assert status == 201
    first_id = first["review_id"]

    (repo / "main.py").write_text("changed\n", encoding="utf-8")

    status, error = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={},
    )

    assert status == 400
    assert "dirty" in error["error"].lower()
    status, active = _request(base_url, "/api/status")
    assert status == 200
    assert active["review_id"] == first_id
    status, history = _request(base_url, "/api/reviews")
    assert status == 200
    assert [review["id"] for review in history["reviews"]] == [first_id]


def test_review_focus_composition_omits_zero_values_and_appends_custom_criteria():
    focus = format_review_focus(
        {"critical_flows": 5, "correctness": 0, "security": 2},
        "Check payment retry behavior\nTrace tenant boundaries",
    )

    assert "Critical flows and impact — priority 5/5 (critical)" in focus
    assert "Security and privacy — priority 2/5 (medium)" in focus
    assert "Correctness and edge cases" not in focus
    assert REVIEW_FOCUS_INSTRUCTIONS["correctness"] not in focus
    assert focus.endswith(
        "Custom criteria (one per line):\n"
        "Check payment retry behavior\n"
        "Trace tenant boundaries"
    )


def test_review_focus_includes_detailed_instructions_for_enabled_criteria():
    assert set(REVIEW_FOCUS_INSTRUCTIONS) == set(REVIEW_FOCUS_CRITERIA)
    assert all(instruction.strip() for instruction in REVIEW_FOCUS_INSTRUCTIONS.values())

    focus = format_review_focus(
        {criterion_id: 5 for criterion_id in REVIEW_FOCUS_CRITERIA},
        "x" * 4000,
    )

    for instruction in REVIEW_FOCUS_INSTRUCTIONS.values():
        assert instruction in focus
    assert len(focus) <= MAX_FOCUS_LENGTH == 6200


def test_review_focus_composition_explains_when_nothing_is_selected():
    assert format_review_focus({"critical_flows": 0}, " \n  ") == (
        "No additional priorities selected; follow the general audit instructions."
    )


def test_review_focus_accepts_the_full_custom_text_limit():
    focus = format_review_focus({}, "x" * 4000)

    assert focus.endswith("Custom criteria (one per line):\n" + "x" * 4000)
    assert len(focus) <= 6000


def test_review_focus_preserves_custom_text_as_entered():
    custom_text = "  Keep leading spaces\nKeep trailing spaces  \n"

    focus = format_review_focus({}, custom_text)

    assert focus.endswith("Custom criteria (one per line):\n" + custom_text)


def test_golden_feature_direction_is_optional_and_requires_all_selected_aspects():
    assert format_golden_feature_direction([]) == ""

    direction = format_golden_feature_direction(
        ["architecture", "third_party_integrations"],
    )

    assert direction.startswith("\n\n## Golden feature direction\n")
    assert "Architecture and maintainability" in direction
    assert "Third-party integrations" in direction
    assert "every selected aspect" in direction


@pytest.mark.parametrize(
    ("review_modes", "included", "omitted"),
    [
        (["code_audit"], ["## Code audit"], ["## Golden Feature"]),
        (["golden_feature"], ["## Golden Feature"], ["## Code audit"]),
        (["code_audit", "golden_feature"], ["## Code audit", "## Golden Feature"], []),
    ],
)
def test_managed_review_persists_modes_and_returns_separate_leaderboards(
    dashboard, tmp_path, review_modes, included, omitted,
):
    server, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )

    status, created = _request(
        base_url, "/api/reviews", token=token, origin=base_url,
        payload={
            "review_modes": review_modes,
            "review_focus": {
                "criteria": {}, "custom_text": "",
                "golden_feature_aspects": ["architecture"],
            },
        },
    )

    assert status == 201
    assert created["manifest"]["review_modes"] == review_modes
    prompt = (
        server.data_directory / "reviews" / created["review_id"] / "prompt.md"
    ).read_text(encoding="utf-8")
    for section in included:
        assert section in prompt
    for section in omitted:
        assert section not in prompt
    status, dashboard_status = _request(base_url, "/api/status")
    assert status == 200
    assert dashboard_status["review_modes"] == review_modes
    assert dashboard_status["leaderboards"] == {
        "code_audit": [], "golden_feature": [],
    }


def test_managed_review_rejects_empty_or_unknown_modes(dashboard, tmp_path):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    _request(
        base_url, "/api/settings/repository", token=token, origin=base_url,
        payload={"path": str(repo)},
    )
    for review_modes in ([], ["unknown"], "code_audit"):
        status, result = _request(
            base_url, "/api/reviews", token=token, origin=base_url,
            payload={"review_modes": review_modes},
        )
        assert status == 400
        assert result["error"]
    assert not (tmp_path / "arena-data" / "reviews").exists()


@pytest.mark.parametrize(
    "aspects",
    [None, "architecture", ["unknown"], ["architecture", "architecture"], [None]],
)
def test_golden_feature_direction_rejects_invalid_aspects(aspects):
    with pytest.raises(ValueError):
        format_golden_feature_direction(aspects)


@pytest.mark.parametrize(
    ("criteria", "custom_text"),
    [
        ({"unknown": 1}, ""),
        ({"critical_flows": -1}, ""),
        ({"critical_flows": 6}, ""),
        ({"critical_flows": True}, ""),
        ({}, "x" * 4001),
        ([], ""),
    ],
)
def test_review_focus_composition_rejects_invalid_values(criteria, custom_text):
    with pytest.raises(ValueError):
        format_review_focus(criteria, custom_text)


def test_dashboard_starts_without_workspace_and_can_open_one(dashboard, tmp_path):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")

    status, configuration = _request(base_url, "/api/status")
    assert status == 200
    assert configuration["models"] == []
    assert configuration["repository_path"] is None
    assert configuration["repository_available"] is False
    assert "--models" in configuration["models_error"]
    status, data = _request(base_url, "/api/data")
    assert status == 409
    assert "workspace" in data["error"].lower()

    status, opened = _request(
        base_url, "/api/workspace/open", token=token, origin=base_url,
        payload={"path": str(workspace)},
    )

    assert status == 200
    assert opened["manifest"]["repo_name"] == "demo"
    status, prompt = _request(base_url, "/api/prompt")
    assert status == 200
    assert prompt["prompt"] == "Review this repository."
    status, data = _request(base_url, "/api/data")
    assert status == 200
    assert data["manifest"]["commit"] == "abc123"


def test_workspace_changes_require_token_and_same_origin(dashboard, tmp_path):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")

    status, _ = _request(base_url, "/api/workspace/open", payload={"path": str(workspace)})
    assert status == 403
    status, _ = _request(
        base_url, "/api/workspace/open", token=token, origin="http://evil.example",
        payload={"path": str(workspace)},
    )
    assert status == 403
    status, data = _request(base_url, "/api/data")
    assert status == 409
    assert "workspace" in data["error"].lower()

    repo = _git_repo(tmp_path / "project")
    output = tmp_path / "unauthorized-arena"
    payload = {
        "repo": str(repo), "out": str(output),
        "review_focus": {"criteria": {}, "custom_text": ""},
    }
    status, _ = _request(base_url, "/api/workspace/init", payload=payload)
    assert status == 403
    status, _ = _request(
        base_url, "/api/workspace/init", token=token, origin="http://evil.example",
        payload=payload,
    )
    assert status == 403
    assert not output.exists()


def test_dashboard_initializes_and_selects_new_workspace(dashboard, tmp_path):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    output = tmp_path / "new-arena"

    status, result = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={"repo": str(repo), "out": str(output), "focus": "Review risks"},
    )

    assert status == 201
    assert result["manifest"]["repo_name"] == "project"
    assert (output / "prompt.md").is_file()
    assert "Review risks" in (output / "prompt.md").read_text(encoding="utf-8")
    status, data = _request(base_url, "/api/data")
    assert status == 200
    assert data["manifest"]["commit"] == result["manifest"]["commit"]


def test_dashboard_initializes_with_structured_review_focus(dashboard, tmp_path):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    output = tmp_path / "new-arena"

    status, _ = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={
            "repo": str(repo),
            "out": str(output),
            "review_focus": {
                "criteria": {"critical_flows": 5, "correctness": 0, "security": 2},
                "custom_text": "Check payment retry behavior\nTrace tenant boundaries",
                "golden_feature_aspects": ["user_facing_features"],
            },
        },
    )

    assert status == 201
    prompt = (output / "prompt.md").read_text(encoding="utf-8")
    assert "Critical flows and impact — priority 5/5 (critical)" in prompt
    assert "Security and privacy — priority 2/5 (medium)" in prompt
    assert "Correctness and edge cases" not in prompt
    assert REVIEW_FOCUS_INSTRUCTIONS["correctness"] not in prompt
    assert "concurrency, input validation, consistency of state" not in prompt
    assert "security boundaries, latency and correctness" not in prompt
    assert (
        "Custom criteria (one per line):\n"
        "Check payment retry behavior\nTrace tenant boundaries"
    ) in prompt
    direction = prompt.split("## Golden feature direction\n", 1)[1].split(
        "\n\n## Repository map", 1
    )[0]
    assert "User-facing functionality" in direction
    assert "every selected aspect" in direction
    assert not (output / "review-focus.json").exists()


def test_dashboard_creates_prompt_with_all_criteria_and_max_custom_text(dashboard, tmp_path):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    output = tmp_path / "new-arena"
    custom_text = "x" * 4000

    status, _ = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={
            "repo": str(repo),
            "out": str(output),
            "review_focus": {
                "criteria": {criterion_id: 5 for criterion_id in REVIEW_FOCUS_CRITERIA},
                "custom_text": custom_text,
            },
        },
    )

    assert status == 201
    prompt = (output / "prompt.md").read_text(encoding="utf-8")
    assert custom_text in prompt
    for instruction in REVIEW_FOCUS_INSTRUCTIONS.values():
        assert instruction in prompt


@pytest.mark.parametrize(
    "review_focus",
    [
        {"criteria": {"unknown": 1}, "custom_text": ""},
        {"criteria": {"security": 9}, "custom_text": ""},
        {"criteria": {"security": True}, "custom_text": ""},
        {"criteria": [], "custom_text": ""},
        {"criteria": {}, "custom_text": "x" * 4001},
        {"criteria": {}, "custom_text": "", "unexpected": "value"},
        {"criteria": {}, "custom_text": "", "golden_feature_aspects": ["unknown"]},
        {"criteria": {}, "custom_text": "", "golden_feature_aspects": "architecture"},
        {"criteria": {}, "custom_text": "", "golden_feature_aspects": None},
        {
            "criteria": {}, "custom_text": "",
            "golden_feature_aspects": ["architecture", "architecture"],
        },
    ],
)
def test_dashboard_rejects_invalid_structured_review_focus(dashboard, tmp_path, review_focus):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    output = tmp_path / "new-arena"

    status, error = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={
            "repo": str(repo), "out": str(output), "review_focus": review_focus,
        },
    )

    assert status == 400
    assert error["error"]
    assert not output.exists()


def test_dashboard_rejects_invalid_workspace_path_without_switching(dashboard, tmp_path):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")
    _request(base_url, "/api/workspace/open", token=token, origin=base_url,
             payload={"path": str(workspace)})

    status, error = _request(
        base_url, "/api/workspace/open", token=token, origin=base_url,
        payload={"path": str(tmp_path / "missing")},
    )

    assert status == 400
    assert error["error"]
    status, data = _request(base_url, "/api/data")
    assert status == 200
    assert data["manifest"]["repo_name"] == "demo"


def test_dashboard_rejects_bad_init_inputs_and_existing_output(dashboard, tmp_path):
    _, base_url, token = dashboard
    existing = _workspace(tmp_path / "already-there")
    repo = _git_repo(tmp_path / "project")

    status, _ = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={"repo": str(tmp_path / "missing"), "out": str(tmp_path / "new")},
    )
    assert status == 400
    status, _ = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={"repo": str(repo), "out": str(existing)},
    )
    assert status == 400
    status, data = _request(base_url, "/api/data")
    assert status == 409
    assert "workspace" in data["error"].lower()


def test_status_lists_only_enabled_model_ids_and_hides_config_values(dashboard, tmp_path):
    _, base_url, _ = dashboard
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [
        {"id": "visible", "model": "test/model", "api_key": "TEST_ONLY_SECRET"},
        {"id": "off", "model": "test/off", "enabled": False},
    ]}), encoding="utf-8")
    dashboard[0].models_path = config

    status, data = _request(base_url, "/api/status")

    assert status == 200
    assert data["models"] == [{"id": "visible"}]
    assert "test/model" not in json.dumps(data)
    assert "TEST_ONLY_SECRET" not in json.dumps(data)


def test_run_requires_confirmation_and_reports_background_results(dashboard, tmp_path, monkeypatch):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")
    _request(base_url, "/api/workspace/open", token=token, origin=base_url,
             payload={"path": str(workspace)})
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [
        {"id": "mock", "model": "test/mock"},
        {"id": "broken", "model": "test/broken"},
        {"id": "disabled", "model": "test/disabled", "enabled": False},
    ]}), encoding="utf-8")
    server = dashboard[0]
    server.models_path = config
    observed = {}

    real_audit_many = app.audit_many

    async def completion(**kwargs):
        if kwargs["model"] == "test/broken":
            raise ConnectionError("provider unavailable")
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps({"findings": [], "golden_feature": {"name": "Feature"}}),
                    tool_calls=[],
                ),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
        )

    async def audit_many(path, models, **kwargs):
        observed["models"] = models
        observed.update(kwargs)
        return await real_audit_many(path, models, completion=completion, **kwargs)

    monkeypatch.setattr(app, "audit_many", audit_many)
    status, _ = _request(
        base_url, "/api/run", token=token, origin=base_url,
        payload={"models": ["mock", "broken"], "confirm_send_code": False},
    )
    assert status == 400

    status, started = _request(
        base_url, "/api/run", token=token, origin=base_url,
        payload={"models": ["mock", "broken"], "confirm_send_code": True,
                 "concurrency": 3, "max_steps": 20, "retries": 1, "retry_delay": 0.5},
    )
    assert status == 202
    job_id = started["job"]["id"]
    for _ in range(50):
        status, result = _request(base_url, f"/api/jobs/{job_id}")
        if result["job"]["status"] in {"succeeded", "partial_failure", "failed"}:
            break
        time.sleep(0.01)

    assert result["job"]["status"] == "partial_failure"
    assert [model["id"] for model in observed["models"]] == ["mock", "broken"]
    assert observed["concurrency"] == 3
    assert observed["max_steps"] == 20
    assert observed["retries"] == 1
    assert observed["retry_delay"] == 0.5
    assert len(result["job"]["outcomes"]) == 2
    assert result["job"]["models"] == [
        {"id": "mock", "status": "succeeded"},
        {"id": "broken", "status": "failed"},
    ]
    assert (workspace / "reports" / "mock.json").is_file()
    assert not (workspace / "reports" / "broken.json").exists()


def test_run_rejects_missing_config_duplicate_and_unauthorized_requests(dashboard, tmp_path, monkeypatch):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")
    _request(base_url, "/api/workspace/open", token=token, origin=base_url,
             payload={"path": str(workspace)})
    called = False

    async def audit_many(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(app, "audit_many", audit_many)
    payload = {"models": ["mock"], "confirm_send_code": True}
    status, _ = _request(base_url, "/api/run", payload=payload)
    assert status == 403
    assert not called

    server = dashboard[0]
    server.models_path = tmp_path / "missing-models.json"
    status, error = _request(base_url, "/api/run", token=token, origin=base_url, payload=payload)
    assert status == 400
    assert "models" in error["error"].lower()

    server.models_path = tmp_path / "models.json"
    server.models_path.write_text(json.dumps({"models": [{"id": "mock", "model": "test/mock"}]}), encoding="utf-8")
    save_json(workspace / "reports" / "mock.json", {
        "findings": [], "golden_feature": {"name": "Feature"},
    })
    status, error = _request(base_url, "/api/run", token=token, origin=base_url, payload=payload)
    assert status == 400
    assert "overwrite" in error["error"].lower()
    assert not called


def test_only_one_audit_can_run_at_a_time(dashboard, tmp_path, monkeypatch):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")
    _request(base_url, "/api/workspace/open", token=token, origin=base_url,
             payload={"path": str(workspace)})
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [{"id": "mock", "model": "test/mock"}]}), encoding="utf-8")
    dashboard[0].models_path = config
    started = Event()
    release = Event()

    async def audit_many(*args, progress, **kwargs):
        progress({"model": "mock", "status": "running"})
        started.set()
        await asyncio.to_thread(release.wait, 3)
        progress({"model": "mock", "status": "succeeded"})
        return [{"model": "mock", "findings": 0}]

    monkeypatch.setattr(app, "audit_many", audit_many)
    payload = {"models": ["mock"], "confirm_send_code": True}
    try:
        status, result = _request(
            base_url, "/api/run", token=token, origin=base_url,
            payload={**payload, "retry_delay": 61},
        )
        assert status == 400
        assert not started.is_set()

        status, result = _request(base_url, "/api/run", token=token, origin=base_url, payload=payload)
        assert status == 202
        assert started.wait(2)
        _, running = _request(base_url, "/api/status")
        assert running["job"]["models"] == [{"id": "mock", "status": "running"}]

        status, error = _request(base_url, "/api/run", token=token, origin=base_url, payload=payload)
        assert status == 409
        assert "already running" in error["error"].lower()
    finally:
        release.set()

    for _ in range(50):
        status, result = _request(base_url, "/api/status")
        if result["job"]["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert result["job"]["status"] == "succeeded"


NODE = shutil.which("node")
NEW_TRANSLATION_KEYS = {
    "contestantsToAudit", "contestantsDescription", "selectAllContestants",
    "clearContestants", "contestantsSelected", "contestantHasReport",
    "noRunnableContestants", "selectAtLeastOneContestant",
    "wizardTitle", "wizardLoading", "wizardProgress", "wizardStepRepository", "wizardStepReview",
    "wizardStepReports", "wizardStepAdjudication", "wizardStepResults",
    "wizardBack", "wizardContinue", "wizardDone", "wizardCurrent", "wizardReady",
    "wizardLocked", "wizardProgressLabel",
    "wizardRepoGate", "wizardReviewGate", "wizardReportsGate", "wizardAuditRunning",
    "wizardFindingsGate", "wizardRubricGate", "wizardRefreshFailed",
    "wizardReviewIntro", "wizardReportsIntro", "wizardAdjudicationIntro",
    "wizardResultsIntro", "partialScoreboard", "newReview", "newReviewHelp", "reviewShort",
    "reviewModes", "reviewModesDescription", "codeAuditMode", "goldenFeatureMode",
    "selectReviewMode", "codeAuditScoreboard", "goldenFeatureScoreboard", "auditScore",
    "featureScore", "proposal",
    "reportSaved",
}
SELECTION_BLOCK = re.compile(
    r"// BEGIN contestant-selection[^\n]*\n(.*?)// END contestant-selection", re.S,
)
WIZARD_BLOCK = re.compile(
    r"// BEGIN wizard-state[^\n]*\n(.*?)// END wizard-state", re.S,
)
REVIEW_MODES_BLOCK = re.compile(
    r"// BEGIN review-modes[^\n]*\n(.*?)// END review-modes", re.S,
)


NEW_REVIEW_BLOCK = re.compile(
    r"// BEGIN new-review[^\n]*\n(.*?)// END new-review", re.S,
)


def _served_page(base_url: str) -> str:
    with urlopen(base_url + "/", timeout=2) as response:
        return response.read().decode()


def _translation_keys(page: str) -> tuple[set[str], set[str]]:
    block = re.search(
        r"const translations = \{\n  en: \{(.*?)\n  \},\n  \"pt-BR\": \{(.*?)\n  \},\n\};",
        page, re.S,
    )
    assert block, "translations object not found"
    key = re.compile(r"^ {4}([A-Za-z0-9_]+):", re.M)
    return set(key.findall(block.group(1))), set(key.findall(block.group(2)))


def test_contestant_picker_is_a_labelled_checkbox_group(dashboard):
    _, base_url, _ = dashboard
    page = _served_page(base_url)

    group = re.search(
        r'<fieldset id="contestants-fieldset" class="contestants"[^>]*>(.*?)</fieldset>',
        page, re.S,
    )
    assert group, "contestant picker fieldset is missing"
    body = group.group(1)
    assert '<legend data-i18n="contestantsToAudit">Contestants to audit</legend>' in body
    assert 'id="contestants-select-all"' in body
    assert 'id="contestants-clear"' in body
    assert 'id="contestants-count" class="meta" role="status"' in body
    assert 'id="run-models"' in body and 'id="models-message"' in body
    assert ('aria-describedby="contestants-description contestants-count models-message"'
            in group.group(0))
    assert "Modelos desta revisão" in page
    assert "reportAlreadyExists" not in page


def test_dashboard_translations_stay_in_sync_and_cover_the_markup(dashboard):
    _, base_url, _ = dashboard
    page = _served_page(base_url)
    english, portuguese = _translation_keys(page)

    assert english == portuguese
    assert NEW_TRANSLATION_KEYS <= english
    used = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([A-Za-z0-9_]+)"', page))
    literal = set(re.findall(r'\bt\("([A-Za-z0-9_]+)"', page))
    literal |= set(re.findall(r'setLocalizedMessage\("[^"]+",\s*"([A-Za-z0-9_]+)"', page))
    # "verdict_" is a dynamic prefix (t("verdict_" + value)), not a key.
    assert (used | {key for key in literal if not key.endswith("_")}) <= english


def test_control_page_exposes_accessible_five_step_wizard(dashboard):
    _, base_url, _ = dashboard
    page = _served_page(base_url)

    assert 'id="wizard-nav"' in page
    assert 'aria-label="Workflow progress"' in page
    assert 'data-wizard-step="0"' in page
    assert 'data-wizard-step="4"' in page
    assert 'data-wizard-panel="0"' in page
    assert 'data-wizard-panel="4"' in page
    assert 'id="partial-scores"' in page
    assert 'id="wizard-message" role="status"' in page
    reports_step = re.search(
        r'data-wizard-panel="2"(.*?)data-wizard-panel="3"', page, re.S,
    )
    assert reports_step and 'id="importmodel"' in reports_step.group(1)
    assert 'id="new-review-button"' in reports_step.group(1)
    assert 'aria-describedby="new-review-help"' in reports_step.group(1)
    mode_picker = re.search(
        r'<fieldset id="review-mode-picker"[^>]*>(.*?)</fieldset>', page, re.S,
    )
    assert mode_picker, "review mode picker is missing"
    assert re.search(r'data-review-mode value="code_audit" checked', mode_picker.group(1))
    assert re.search(r'data-review-mode value="golden_feature" checked', mode_picker.group(1))


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_review_mode_selection_shows_only_matching_configuration():
    block = REVIEW_MODES_BLOCK.search(app.PAGE)
    assert block, "review mode helpers are missing"
    script = block.group(1) + """
const modes = [
  {checked: true, value: "code_audit"},
  {checked: false, value: "golden_feature"},
];
const panels = {
  "audit-focus-config": {}, "golden-feature-aspects": {}, "review-mode-message": {},
};
globalThis.document = {
  querySelectorAll: () => modes,
  getElementById: (id) => panels[id],
};
function setMessage(id, value) { panels[id].textContent = value; }
function updateWorkspaceActions() {}
function t(key) { return key; }
updateReviewModeControls();
const auditOnly = {
  auditHidden: panels["audit-focus-config"].hidden,
  featureHidden: panels["golden-feature-aspects"].hidden,
  message: panels["review-mode-message"].textContent,
};
modes[0].checked = false;
modes[1].checked = false;
updateReviewModeControls();
console.log(JSON.stringify({auditOnly, none: panels["review-mode-message"].textContent}));
"""
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "auditOnly": {"auditHidden": False, "featureHidden": True, "message": ""},
        "none": "selectReviewMode",
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("status", "data", "repository_input", "complete", "unlocked", "max_unlocked"),
    [
        ({}, {}, "", [False, False, False, False], [True, False, False, False, False], 0),
        (
            {"repository_available": True, "repository_path": "C:/repo"}, {}, "C:/repo",
            [True, False, False, False], [True, True, False, False, False], 1,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review"},
            {"manifest": {}}, "C:/repo", [True, True, False, False],
            [True, True, True, False, False], 2,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review",
             "job": {"workspace": "C:/review", "status": "running"}},
            {"manifest": {}, "reports": {"a": {"findings": []}}}, "C:/repo",
            [True, True, False, False], [True, True, True, False, False], 2,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review",
             "job": {"workspace": "C:/other", "status": "running"}},
            {"manifest": {}, "reports": {"a": {"findings": []}}}, "C:/repo",
            [True, True, True, False], [True, True, True, True, False], 3,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review",
             "job": {"workspace": "C:/review", "status": "partial_failure"}},
            {"manifest": {}, "reports": {"a": {"findings": [{"id": "F1"}]}},
             "judgments": {"a": {"findings": {"F1": {"verdict": "confirmed"}}}}},
            "C:/repo", [True, True, True, False], [True, True, True, True, False], 3,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review"},
            {"manifest": {}, "reports": {"a": {"findings": [{"id": "F1"}]}},
             "judgments": {"a": {"findings": {"F1": {"verdict": "pending"}},
                                   "rubric": {"improvements": 0, "gold": 0, "rigor": 0}}}},
            "C:/repo", [True, True, True, False], [True, True, True, True, False], 3,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review"},
            {"manifest": {}, "reports": {"a": {"findings": [{"id": "F1"}]}},
             "judgments": {"a": {"findings": {"F1": {"verdict": "false_positive"}},
                                   "rubric": {"improvements": 0, "gold": 0, "rigor": 0}}}},
            "C:/repo", [True, True, True, True], [True, True, True, True, True], 4,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review"},
            {"manifest": {}, "reports": {"a": {"findings": []}}, "judgments": {}}, "C:/repo",
            [True, True, True, False], [True, True, True, True, False], 3,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review"},
            {"manifest": {}, "reports": {"a": {"findings": []}},
             "judgments": {"a": {"rubric": {"improvements": 0, "gold": 0, "rigor": 0}}}},
            "C:/changed", [False, False, False, False], [True, False, False, False, False], 0,
        ),
        (
            {"repository_available": True, "repository_path": "C:/repo", "workspace": "C:/review"},
            {"manifest": {}, "reports": {"a": {"findings": []}, "b": {"findings": []}},
             "judgments": {"a": {"rubric": {"improvements": 0, "gold": 0, "rigor": 0}}}},
            "C:/repo", [True, True, True, False], [True, True, True, True, False], 3,
        ),
    ],
)
def test_wizard_progress_is_derived_from_current_review_state(
    status, data, repository_input, complete, unlocked, max_unlocked,
):
    block = WIZARD_BLOCK.search(app.PAGE)
    assert block, "wizard-state block is missing"
    script = (
        block.group(1)
        + f"\nconst result = deriveWizardState({json.dumps(status)}, {json.dumps(data)}, "
        + f"{json.dumps(repository_input)});\nconsole.log(JSON.stringify(result));\n"
    )
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    progress = json.loads(result.stdout)
    assert progress["complete"] == complete
    assert progress["unlocked"] == unlocked
    assert progress["maxUnlocked"] == max_unlocked


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("review_modes", "report", "judgment"),
    [
        (
            ["code_audit"],
            {"findings": []},
            {"rubric": {"improvements": 0, "rigor": 0}},
        ),
        (
            ["golden_feature"],
            {"findings": [], "golden_feature": {"name": "Feature"}},
            {"rubric": {"gold": 10}},
        ),
    ],
)
def test_wizard_requires_only_scores_for_enabled_modes(review_modes, report, judgment):
    block = WIZARD_BLOCK.search(app.PAGE)
    assert block, "wizard-state block is missing"
    data = {
        "manifest": {"review_modes": review_modes},
        "reports": {"a": report}, "judgments": {"a": judgment},
    }
    script = (
        block.group(1)
        + "\nconst result = deriveWizardState({repository_available:true, "
        + f"repository_path:'C:/repo', workspace:'C:/review'}}, {json.dumps(data)}, "
        + "'C:/repo');\nconsole.log(JSON.stringify(result));\n"
    )
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    progress = json.loads(result.stdout)
    assert progress["complete"] == [True, True, True, True]
    assert progress["findingsReady"] is True
    assert progress["rubricsReady"] is True


def test_manual_report_import_works_without_provider_models(dashboard, tmp_path):
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "manual-review")
    status, _ = _request(
        base_url, "/api/workspace/open", token=token, origin=base_url,
        payload={"path": str(workspace)},
    )
    assert status == 200

    report = {
        "summary": "Manual review",
        "golden_feature": {"name": "Clearer validation", "mechanism": ""},
        "findings": [],
    }
    status, result = _request(
        base_url, "/api/import", token=token, origin=base_url,
        payload={"model": "manual", "content": json.dumps(report)},
    )
    assert status == 200
    assert result["ok"] is True

    status, data = _request(base_url, "/api/data")
    assert status == 200
    assert data["reports"]["manual"]["audit_metadata"]["source"] == "manual"
    assert data["review_modes"] == ["code_audit", "golden_feature"]
    assert list(data["leaderboards"]) == ["code_audit", "golden_feature"]


@pytest.mark.parametrize(
    ("review_mode", "report", "rubric", "board_key", "expected_score"),
    [
        (
            "code_audit",
            {"summary": "Audit only", "findings": [], "improvements": []},
            {"improvements": 8, "rigor": 3}, "code_audit", 11,
        ),
        (
            "golden_feature",
            {"summary": "Feature only", "golden_feature": {"name": "Feature"}},
            {"gold": 14}, "golden_feature", 14,
        ),
    ],
)
def test_manual_import_and_rubric_follow_selected_mode(
    dashboard, tmp_path, review_mode, report, rubric, board_key, expected_score,
):
    _, base_url, token = dashboard
    repo = _git_repo(tmp_path / "project")
    workspace = tmp_path / "mode-review"
    status, created = _request(
        base_url, "/api/workspace/init", token=token, origin=base_url,
        payload={"repo": str(repo), "out": str(workspace), "review_modes": [review_mode]},
    )
    assert status == 201
    assert created["review_modes"] == [review_mode]

    status, imported = _request(
        base_url, "/api/import", token=token, origin=base_url,
        payload={"model": "manual", "content": json.dumps(report)},
    )
    assert status == 200
    assert imported["ok"] is True
    status, saved = _request(
        base_url, "/api/rubric", token=token, origin=base_url,
        payload={"model": "manual", **rubric},
    )
    assert status == 200
    assert saved["ok"] is True

    status, data = _request(base_url, "/api/data")
    assert status == 200
    assert set(data["leaderboards"]) == {"code_audit", "golden_feature"}
    other_board = "golden_feature" if board_key == "code_audit" else "code_audit"
    assert data["leaderboards"][other_board] == []
    assert data["leaderboards"][board_key][0]["total"] == expected_score
    status, dashboard_status = _request(base_url, "/api/status")
    assert status == 200
    assert dashboard_status["review_modes"] == [review_mode]
    assert dashboard_status["leaderboards"] == data["leaderboards"]


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("previous", "models", "reports", "runnable", "selected"),
    [
        # First render for an arena ticks every runnable contestant.
        (None, ["a", "b", "c"], {"b": {}}, ["a", "c"], ["a", "c"]),
        # A user's subset survives a re-render (poll, language toggle, refresh).
        (["a"], ["a", "b", "c"], {}, ["a", "b", "c"], ["a"]),
        # "Clear selection" ([]) must not fall back to the default.
        ([], ["a", "b"], {}, ["a", "b"], []),
        # A contestant that gained a report is pruned from the selection.
        (["a", "b"], ["a", "b"], {"b": {}}, ["a"], ["a"]),
        # A model added to models.json later starts unticked.
        (["a"], ["a", "d"], {}, ["a", "d"], ["a"]),
        # Valid ids that collide with Object.prototype members are not "locked".
        (None, ["constructor", "toString"], {}, ["constructor", "toString"],
         ["constructor", "toString"]),
        (None, [], {}, [], []),
    ],
)
def test_contestant_selection_reconciliation(previous, models, reports, runnable, selected):
    block = SELECTION_BLOCK.search(app.PAGE)
    assert block, "contestant-selection block is missing"
    case = {"previous": previous, "models": [{"id": m} for m in models], "reports": reports}
    script = (
        block.group(1)
        + f"\nconst c = {json.dumps(case)};\n"
        + "console.log(JSON.stringify("
        + "reconcileContestantSelection(c.previous, c.models, c.reports)));\n"
    )
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"runnable": runnable, "selected": selected}


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_new_review_resets_focus_and_refuses_to_start_during_an_audit():
    block = NEW_REVIEW_BLOCK.search(app.PAGE)
    assert block, "new-review block is missing"
    script = block.group(1) + """
const sliders = [
  {value: "1", defaultValue: "5", dataset: {priorityId: "security"}},
  {value: "0", defaultValue: "3", dataset: {priorityId: "architecture"}},
];
const customText = {value: "old criteria"};
const goldenAspects = [
  {checked: true, value: "architecture"},
  {checked: false, value: "user_facing_features"},
];
const modes = [
  {checked: false, value: "code_audit"},
  {checked: false, value: "golden_feature"},
];
const repositoryInput = {value: "C:/repo"};
const elements = {
  "focus-custom-text": customText, "repo-path": repositoryInput,
  "audit-focus-config": {}, "golden-feature-aspects": {}, "review-mode-message": {},
};
globalThis.document = {
  querySelectorAll: (selector) => selector === "[data-golden-feature-aspect]" ? goldenAspects
    : selector === "[data-review-mode]" ? modes : sliders,
  getElementById: (id) => elements[id],
};
function updatePriorityLabel(slider) { slider.label = slider.value; }
function updateReviewModeControls() {}
function updateWorkspaceActions() {}
function jobIsActive(job) { return !!job && ["queued", "running"].includes(job.status); }
function t(key) { return key; }
function setMessage() {}
let visitedStep = null;
function goToWizardStep(index) { visitedStep = index; }
let reviewCreationInProgress = false;
let dashboardStatus = {
  repository_available: true,
  repository_path: "C:/repo",
  workspace: "C:/review-a",
  job: null,
};
function attempt() {
  sliders[0].value = "1";
  sliders[1].value = "0";
  customText.value = "old criteria";
  goldenAspects[0].checked = true;
  goldenAspects[1].checked = true;
  visitedStep = null;
  prepareNewReview();
  return {
    values: sliders.map((slider) => slider.value), custom: customText.value,
    aspects: goldenAspects.map((aspect) => aspect.checked),
    modes: modes.map((mode) => mode.checked), step: visitedStep,
  };
}
const ready = attempt();
dashboardStatus.job = {status: "running"};
const running = attempt();
dashboardStatus.job = null;
dashboardStatus.repository_available = false;
const unavailable = attempt();
console.log(JSON.stringify({ready, running, unavailable}));
"""
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "ready": {
            "values": ["5", "3"], "custom": "", "aspects": [False, False],
            "modes": [True, True], "step": 1,
        },
        "running": {
            "values": ["1", "0"], "custom": "old criteria", "aspects": [True, True],
            "modes": [True, True],
            "step": None,
        },
        "unavailable": {
            "values": ["1", "0"], "custom": "old criteria", "aspects": [True, True],
            "modes": [True, True],
            "step": None,
        },
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_create_review_sends_selected_golden_feature_aspects():
    function = re.search(r"async function createReview\(\) \{.*?\n\}", app.PAGE, re.S)
    assert function, "createReview function is missing"
    script = function.group(0) + """
let reviewCreationInProgress = false;
const sliders = [{value: "5", dataset: {priorityId: "security"}}];
const aspects = [
  {checked: true, value: "architecture"},
  {checked: false, value: "user_facing_features"},
  {checked: true, value: "third_party_integrations"},
];
const button = {
  disabled: false,
  setAttribute() {},
  removeAttribute() {},
  textContent: "Create review",
};
const elements = {
  "create-review-button": button,
  "focus-custom-text": {value: "Check retries"},
};
globalThis.document = {
  getElementById: (id) => elements[id],
  querySelectorAll: (selector) => selector === ".priority-slider" ? sliders : aspects,
};
let request;
async function api(path, body) { request = {path, body}; }
function t(key) { return key; }
function setLocalizedMessage() {}
function updateWorkspaceActions() {}
function setRepositoryError() {}
async function reload() {}
const modes = [
  {checked: true, value: "code_audit"},
  {checked: false, value: "golden_feature"},
];
function selectedReviewModes() {
  return modes.filter((mode) => mode.checked).map((mode) => mode.value);
}
document.querySelectorAll = (selector) => selector === "[data-review-mode]"
  ? modes : selector === ".priority-slider" ? sliders : aspects;
createReview().then(() => console.log(JSON.stringify(request)));
"""
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "path": "/api/reviews",
        "body": {
            "review_modes": ["code_audit"],
            "review_focus": {
                "criteria": {"security": 5},
                "custom_text": "Check retries",
                "golden_feature_aspects": [],
            },
        },
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_dashboard_script_is_syntactically_valid(dashboard, tmp_path):
    _, base_url, _ = dashboard
    script = re.search(r"</main><script>(.*)</script></body>", _served_page(base_url), re.S)
    assert script, "dashboard script not found"
    path = tmp_path / "dashboard.js"
    path.write_text(script.group(1), encoding="utf-8")
    result = subprocess.run(
        [NODE, "--check", str(path)], capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("selection", [[], ["off"], ["ghost"], ["mock", "mock"], "mock", [1]])
def test_run_rejects_invalid_contestant_selections(dashboard, tmp_path, monkeypatch, selection):
    """The server stays the authority even though the client now guards an empty selection."""
    _, base_url, token = dashboard
    workspace = _workspace(tmp_path / "arena")
    _request(base_url, "/api/workspace/open", token=token, origin=base_url,
             payload={"path": str(workspace)})
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [
        {"id": "mock", "model": "test/mock"},
        {"id": "off", "model": "test/off", "enabled": False},
    ]}), encoding="utf-8")
    dashboard[0].models_path = config
    called = False

    async def audit_many(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(app, "audit_many", audit_many)
    status, error = _request(
        base_url, "/api/run", token=token, origin=base_url,
        payload={"models": selection, "confirm_send_code": True},
    )

    assert status == 400
    assert error["error"]
    assert not called
