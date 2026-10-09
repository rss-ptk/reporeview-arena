"""Freeze an auditable, redacted view of a *clean* Git checkout."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

# Defense-in-depth denylist. A general secret scanner cannot guarantee no secrets
# in source files; the user must review target code before explicitly uploading it.
DENIED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "logs", "dist", "build", ".pytest_cache",
    "__pycache__", ".next", "coverage", "recordings", "fixtures_private", ".ssh",
}
DENIED_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "*secret*", "*credential*", "id_rsa*", "id_ed25519*", "*.sqlite", "*.db",
    "*.log", "*.csv", "*.parquet", "*.jsonl", "*.gz", "*.zip", "*.tar*",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.pdf", "*.exe", "*.dll",
    "*.so", "*.mp4", "*.mp3", "*.woff*", "*.ttf", "*.lock",
)
MAX_FILE_BYTES = 180_000
MAX_TOTAL_BYTES = 16_000_000


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True,
        timeout=15,
    )
    return process.stdout.decode("utf-8", "replace").strip()


def _allowed(path: Path) -> bool:
    if any(part.lower() in DENIED_PARTS for part in path.parts):
        return False
    return not any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in DENIED_GLOBS)


def _detect_languages(files: list[str]) -> list[str]:
    kinds = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".rs": "Rust", ".go": "Go", ".java": "Java", ".cs": "C#", ".cpp": "C++",
        ".c": "C", ".rb": "Ruby", ".php": "PHP", ".kt": "Kotlin", ".swift": "Swift",
    }
    return sorted({kinds[Path(f).suffix.lower()] for f in files if Path(f).suffix.lower() in kinds})


def snapshot_repo(repo: Path, destination: Path) -> dict[str, Any]:
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError("--repo must be an existing directory")
    root = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    if root != repo:
        raise ValueError("Pass the Git repository root, not a subdirectory")
    # --untracked-files=all prevents hidden local files from silently contaminating the snapshot.
    # Ignore untracked files, but refuse any tracked staged/unstaged differences.
    dirty = _git(repo, "diff", "--name-only", "HEAD")
    if dirty:
        raise ValueError("Git tracked files are dirty; commit or stash changes first")
    sha = _git(repo, "rev-parse", "HEAD")
    raw = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True, check=True, timeout=15,
    ).stdout
    names = [os.fsdecode(v) for v in raw.split(b"\x00") if v]
    snapshot = destination / "snapshot"
    if destination.exists():
        raise FileExistsError(f"Workspace already exists: {destination}")
    snapshot.mkdir(parents=True)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total = 0
    for name in sorted(names):
        rel = Path(name)
        source = repo / rel
        if not _allowed(rel):
            skipped.append({"path": name, "reason": "denylist"})
            continue
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(repo):
            skipped.append({"path": name, "reason": "not regular local file"})
            continue
        size = source.stat().st_size
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            skipped.append({"path": name, "reason": "size limit"})
            continue
        blob = source.read_bytes()
        if b"\x00" in blob[:4096]:
            skipped.append({"path": name, "reason": "binary"})
            continue
        try:
            contents = blob.decode("utf-8-sig")
        except UnicodeDecodeError:
            skipped.append({"path": name, "reason": "non UTF-8"})
            continue
        target = snapshot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        kept.append({"path": name, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()})
        total += size
    return {
        "repo_name": repo.name,
        "original_path": str(repo),
        "commit": sha,
        "languages": _detect_languages([v["path"] for v in kept]),
        "file_count": len(kept),
        "bytes": total,
        "files": kept,
        "skipped": skipped,
    }


class RepositoryReader:
    """Bounded read-only toolset; no shell, write, network or symlink traversal."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()
        )
        self.allowed = set(self.files)

    def list_files(self, prefix: str = "", limit: int = 150) -> str:
        """List file paths with an optional prefix."""
        limit = max(1, min(int(limit), 250))
        names = [f for f in self.files if f.startswith(prefix)]
        return "\n".join(names[:limit]) + f"\n[{len(names)} matches; showing {min(len(names),limit)}]"

    def read_file(self, path: str, start_line: int = 1, end_line: int = 220) -> str:
        """Read numbered lines of a project file."""
        if path not in self.allowed:
            return "ERROR: file not in approved repository snapshot"
        start_line = max(1, int(start_line))
        end_line = min(start_line + 239, max(start_line, int(end_line)))
        lines = (self.root / path).read_text(encoding="utf-8").splitlines()
        return "\n".join(f"{i+1}: {lines[i]}" for i in range(start_line - 1, min(end_line, len(lines)))) or "(empty)"

    def search_code(self, query: str, limit: int = 80) -> str:
        """Search project files for a literal phrase (case-insensitive)."""
        if not query or len(query) > 120:
            return "ERROR: query must be 1..120 characters"
        limit = max(1, min(int(limit), 150))
        pattern = re.compile(re.escape(query), re.I)
        matches = []
        for path in self.files:
            for num, line in enumerate((self.root / path).read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    matches.append(f"{path}:{num}: {line[:300]}")
                    if len(matches) >= limit:
                        return "\n".join(matches) + "\n[limit reached]"
        return "\n".join(matches) if matches else "(no matches)"

    def describe(self, max_chars: int = 15_000) -> str:
        return self.list_files(limit=1000)[:max_chars]
