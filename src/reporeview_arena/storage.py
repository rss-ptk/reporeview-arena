"""Filesystem-backed persistent competition artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".tmp-", delete=False
    ) as fh:
        temp = Path(fh.name)
        json.dump(value, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def workspace_file(workspace: Path, file: str) -> Path:
    workspace = workspace.resolve()
    requested = (workspace / file).resolve()
    if not requested.is_relative_to(workspace):
        raise ValueError("Workspace path escapes workspace")
    return requested
