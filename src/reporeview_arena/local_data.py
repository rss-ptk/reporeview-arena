"""Fixed per-user storage for repository settings and dashboard reviews."""

from __future__ import annotations

import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .repository import validate_repository_root
from .storage import load_json, save_json

SETTINGS_FILE = "settings.json"
REVIEWS_DIRECTORY = "reviews"
_REVIEW_ID = re.compile(r"^[0-9a-f]{32}$")


def app_data_directory(
    *,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the OS-standard application data directory without creating it."""

    platform = sys.platform if platform is None else platform
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    if platform == "win32":
        base = Path(environment.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "RepoReviewArena"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "RepoReviewArena"
    base = Path(environment.get("XDG_DATA_HOME") or home / ".local" / "share")
    if not base.is_absolute():
        base = home / ".local" / "share"
    return base / "reporeview-arena"


def load_settings(data_directory: Path) -> dict[str, str | None]:
    """Load the one persisted setting, defaulting to no selected repository."""

    path = Path(data_directory) / SETTINGS_FILE
    if not path.exists():
        return {"repository_path": None}
    settings = load_json(path)
    if set(settings) != {"repository_path"}:
        raise ValueError("settings.json must contain only repository_path")
    repository_path = settings["repository_path"]
    if repository_path is not None:
        if not isinstance(repository_path, str) or not Path(repository_path).is_absolute():
            raise ValueError("repository_path must be an absolute path or null")
    return {"repository_path": repository_path}


def save_repository_path(data_directory: Path, repository_path: Path) -> Path:
    """Validate and atomically persist the selected Git checkout root."""

    repository_path = validate_repository_root(repository_path)
    save_json(Path(data_directory) / SETTINGS_FILE, {
        "repository_path": str(repository_path),
    })
    return repository_path


def create_review_id() -> str:
    """Return a filesystem-safe, collision-resistant review identifier."""

    return uuid.uuid4().hex


def review_directory(data_directory: Path, review_id: str) -> Path:
    """Resolve a managed review ID directly under the app's reviews directory."""

    if not isinstance(review_id, str) or not _REVIEW_ID.fullmatch(review_id):
        raise ValueError("Invalid review ID")
    data_root = Path(data_directory).resolve()
    reviews_path = data_root / REVIEWS_DIRECTORY
    if reviews_path.is_symlink():
        raise ValueError("Reviews directory must be inside the app data directory")
    root = reviews_path.resolve()
    if not root.is_relative_to(data_root):
        raise ValueError("Reviews directory must be inside the app data directory")
    requested = (root / review_id).resolve()
    if not requested.is_relative_to(root):
        raise ValueError("Review path escapes the reviews directory")
    return requested


def list_reviews(
    data_directory: Path,
    repository_path: Path | None = None,
) -> list[dict[str, str]]:
    """List valid managed reviews, optionally filtered to one repository root."""

    data_root = Path(data_directory).resolve()
    reviews_path = data_root / REVIEWS_DIRECTORY
    if reviews_path.is_symlink():
        return []
    root = reviews_path.resolve()
    if not root.is_relative_to(data_root):
        return []
    if not root.is_dir():
        return []
    repository_root = repository_path.resolve() if repository_path is not None else None
    reviews: list[tuple[datetime, dict[str, str]]] = []
    try:
        folders = list(root.iterdir())
    except OSError:
        return []
    for folder in folders:
        if (folder.is_symlink() or not folder.is_dir()
                or not _REVIEW_ID.fullmatch(folder.name)):
            continue
        try:
            if not folder.resolve().is_relative_to(root):
                continue
            manifest = load_json(folder / "manifest.json")
            repo_name = manifest.get("repo_name")
            original_path = manifest.get("original_path")
            commit = manifest.get("commit")
            created_at = manifest.get("created_at")
            if not all(isinstance(value, str) and value for value in
                       (repo_name, original_path, commit)):
                continue
            if repository_root is not None and Path(original_path).resolve() != repository_root:
                continue
            sort_time: datetime | None = None
            if isinstance(created_at, str):
                try:
                    sort_time = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00"),
                    )
                except ValueError:
                    pass
            if sort_time is None:
                sort_time = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc)
            elif sort_time.tzinfo is None:
                sort_time = sort_time.replace(tzinfo=timezone.utc)
            sort_time = sort_time.astimezone(timezone.utc)
            created_at = sort_time.isoformat()
        except (OSError, RuntimeError, ValueError, TypeError):
            continue
        reviews.append((sort_time, {
            "id": folder.name,
            "repo_name": repo_name,
            "commit": commit,
            "created_at": created_at,
        }))
    return [review for _, review in sorted(reviews, key=lambda item: item[0], reverse=True)]
