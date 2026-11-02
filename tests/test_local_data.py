from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from reporeview_arena.local_data import (
    app_data_directory,
    create_review_id,
    list_reviews,
    load_settings,
    review_directory,
    save_repository_path,
)
from reporeview_arena.storage import save_json


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    (path / "main.py").write_text("print('demo')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "main.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "demo"], check=True)
    return path


@pytest.mark.parametrize(
    ("platform", "environment", "expected"),
    [
        ("win32", {"LOCALAPPDATA": "C:/Users/test/AppData/Local"}, "C:/Users/test/AppData/Local/RepoReviewArena"),
        ("darwin", {}, "C:/Users/test/Library/Application Support/RepoReviewArena"),
        ("linux", {"XDG_DATA_HOME": "C:/Users/test/.local/share"}, "C:/Users/test/.local/share/reporeview-arena"),
    ],
)
def test_app_data_directory_uses_the_platform_default(platform, environment, expected):
    result = app_data_directory(platform=platform, environment=environment, home=Path("C:/Users/test"))

    assert result == Path(expected)


def test_settings_store_only_the_absolute_repository_path(tmp_path):
    repo = _git_repo(tmp_path / "project")
    data_directory = tmp_path / "app-data"

    assert load_settings(data_directory) == {"repository_path": None}
    saved = save_repository_path(data_directory, repo)

    assert saved == repo.resolve()
    assert load_settings(data_directory) == {"repository_path": str(repo.resolve())}
    assert set(load_settings(data_directory)) == {"repository_path"}


def test_repository_setting_rejects_nested_non_repo_and_missing_paths(tmp_path):
    repo = _git_repo(tmp_path / "project")
    nested = repo / "nested"
    nested.mkdir()
    data_directory = tmp_path / "app-data"

    with pytest.raises(ValueError, match="repository root"):
        save_repository_path(data_directory, nested)
    with pytest.raises(ValueError, match="Git repository"):
        save_repository_path(data_directory, tmp_path)
    with pytest.raises(ValueError):
        save_repository_path(data_directory, tmp_path / "missing")


def test_review_directory_ids_are_scoped_and_history_is_filtered_by_repository(tmp_path):
    repo = _git_repo(tmp_path / "project")
    other_repo = _git_repo(tmp_path / "other")
    data_directory = tmp_path / "app-data"
    first_id = create_review_id()
    second_id = create_review_id()
    first = data_directory / "reviews" / first_id
    second = data_directory / "reviews" / second_id
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    save_json(first / "manifest.json", {
        "repo_name": "project", "original_path": str(repo.resolve()),
        "commit": "a" * 40, "created_at": "2026-11-01T12:00:00+00:00",
    })
    save_json(second / "manifest.json", {
        "repo_name": "other", "original_path": str(other_repo.resolve()),
        "commit": "b" * 40, "created_at": "2026-11-02T12:00:00+00:00",
    })

    assert review_directory(data_directory, first_id) == first.resolve()
    assert [item["id"] for item in list_reviews(data_directory, repo)] == [first_id]
    assert [item["id"] for item in list_reviews(data_directory)] == [second_id, first_id]
    with pytest.raises(ValueError):
        review_directory(data_directory, "../settings.json")


def test_review_history_ignores_symlinked_review_directories(tmp_path):
    repo = _git_repo(tmp_path / "project")
    data_directory = tmp_path / "app-data"
    external_review = tmp_path / "external-review"
    external_review.mkdir()
    save_json(external_review / "manifest.json", {
        "repo_name": "outside", "original_path": str(repo.resolve()),
        "commit": "c" * 40, "created_at": "2026-11-02T12:00:00+00:00",
    })
    managed_root = data_directory / "reviews"
    managed_root.mkdir(parents=True)
    linked_review = managed_root / create_review_id()
    try:
        os.symlink(external_review, linked_review, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    assert list_reviews(data_directory, repo) == []


def test_review_directory_rejects_a_symlinked_reviews_root(tmp_path):
    data_directory = tmp_path / "app-data"
    data_directory.mkdir()
    external_root = tmp_path / "external-reviews"
    external_root.mkdir()
    try:
        os.symlink(external_root, data_directory / "reviews", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    with pytest.raises(ValueError, match="inside the app data directory"):
        review_directory(data_directory, create_review_id())
    assert list_reviews(data_directory) == []
