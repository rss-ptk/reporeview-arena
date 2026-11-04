"""Command line UX for initialization, running, import, scoring and web review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .app import _data, serve
from .modes import rubric_fields_for_modes
from .orchestrator import audit_many, model_slug
from .reports import parse_report, validate_locations
from .storage import load_json, save_json
from .usage import read_usage
from .workspace import DEFAULT_FOCUS, initialize_workspace


def _load_provider_environment() -> None:
    dotenv_path = Path.cwd() / ".env"
    if not dotenv_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        print(
            "A .env file was found, but python-dotenv is unavailable. "
            "Install the [llm] extra or export provider keys in this shell.",
            file=sys.stderr,
        )
        return
    load_dotenv(dotenv_path=dotenv_path, override=False)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="reporeview", description="Multi-LLM code audit arena")
    command = root.add_subparsers(dest="command", required=True)
    init = command.add_parser("init", help="Create a frozen audit workspace for a clean Git checkout")
    init.add_argument("--repo", required=True, type=Path)
    init.add_argument("--out", required=True, type=Path)
    init.add_argument("--focus", default=DEFAULT_FOCUS)
    audit = command.add_parser("run", help="Audit concurrently using configured BYOK models")
    audit.add_argument("--workspace", required=True, type=Path)
    audit.add_argument("--models", required=True, type=Path, help="Path to models.json")
    audit.add_argument("--concurrency", type=int, default=2)
    audit.add_argument("--max-steps", type=int, default=35)
    audit.add_argument("--retries", type=int, choices=range(6), default=2,
                       help="Retries per provider call for temporary failures (default: 2)")
    audit.add_argument("--retry-delay", type=float, default=5,
                       help="Initial retry delay in seconds, 0-60; doubles up to 60 (default: 5)")
    audit.add_argument("--confirm-send-code", action="store_true", help="Explicitly authorize selected providers to receive approved source fragments")
    imp = command.add_parser("import", help="Import a structured JSON report from VS Code or elsewhere")
    imp.add_argument("--workspace", required=True, type=Path)
    imp.add_argument("--model", required=True)
    imp.add_argument("--file", required=True, type=Path)
    web = command.add_parser("web", help="Launch the local workspace, audit and review dashboard")
    web.add_argument("--workspace", type=Path, help="Optional initial audit workspace")
    web.add_argument(
        "--models", type=Path,
        help="Path to models.json for web audits (defaults to ./models.json when present)",
    )
    web.add_argument("--port", type=int, default=8765)
    score = command.add_parser("score", help="Output current validated scorecard as JSON")
    score.add_argument("--workspace", required=True, type=Path)
    usage = command.add_parser("usage", help="Show recorded usage and cost as JSON")
    usage.add_argument("--workspace", required=True, type=Path)
    usage.add_argument("--run-id", help="Filter by execution ID")
    prompt = command.add_parser("prompt", help="Show generated prompt for pasting into an IDE")
    prompt.add_argument("--workspace", required=True, type=Path)
    judge = command.add_parser("judge", help="Adjudicate one finding on the CLI")
    judge.add_argument("--workspace", required=True, type=Path)
    judge.add_argument("--model", required=True)
    judge.add_argument("--finding", required=True)
    judge.add_argument("--verdict", choices=["confirmed", "false_positive", "pending"], required=True)
    judge.add_argument("--severity", choices=["P0", "P1", "P2", "P3"])
    judge.add_argument("--issue-key", default="")
    judge.add_argument("--fabricated-evidence", action="store_true")
    rubric = command.add_parser("rubric", help="Set human-rated improvements, gold and rigor scores")
    rubric.add_argument("--workspace", required=True, type=Path)
    rubric.add_argument("--model", required=True)
    rubric.add_argument("--improvements", type=int)
    rubric.add_argument("--gold", type=int)
    rubric.add_argument("--rigor", type=int)
    return root


def execute(args: argparse.Namespace) -> None:
    if args.command == "init":
        manifest = initialize_workspace(args.repo, args.out, args.focus)
        print(json.dumps({"workspace": str(args.out), "commit": manifest["commit"],
                          "files": manifest["file_count"], "skipped": len(manifest["skipped"])}, indent=2))
        print("Review manifest.json excluded files. The snapshot is for external LLM sharing.")
        return
    if args.command == "web":
        models_path = args.models
        if models_path is None:
            default_models_path = Path("models.json")
            if default_models_path.is_file():
                models_path = default_models_path.resolve()
        if models_path is not None:
            _load_provider_environment()
        serve(args.workspace, port=args.port, models=models_path)
        return
    workspace = args.workspace.resolve()
    if not (workspace / "manifest.json").exists():
        raise ValueError("Workspace not initialized; run 'reporeview init' first")
    if args.command == "prompt":
        print((workspace / "prompt.md").read_text(encoding="utf-8"))
    elif args.command == "usage":
        print(json.dumps(read_usage(workspace, args.run_id), indent=2))
    elif args.command == "run":
        if not args.confirm_send_code:
            raise ValueError("Sending source requires --confirm-send-code")
        if not 0 <= args.retry_delay <= 60:
            raise ValueError("--retry-delay must be between 0 and 60 seconds")
        config = load_json(args.models)
        models = config.get("models", [])
        if not models:
            raise ValueError("models.json must contain a nonempty models array")
        approved = [m for m in models if m.get("enabled", True)]
        for m in approved:
            model_slug(m["id"])
            if not m.get("model"):
                raise ValueError("Each model must specify its LiteLLM model name")
            if (workspace / "reports" / f"{m['id']}.json").exists():
                raise ValueError(f"Refusing to overwrite an existing audit: {m['id']}")
        _load_provider_environment()
        print(f"Sending read-only snapshot fragments to {len(approved)} model providers.")
        outcome = asyncio.run(audit_many(workspace, approved, concurrency=args.concurrency,
                                        max_steps=args.max_steps, retries=args.retries,
                                        retry_delay=args.retry_delay))
        print(json.dumps(outcome, indent=2))
        failed = sum("error" in item for item in outcome)
        if failed:
            raise RuntimeError(f"{failed} model audits failed; {len(outcome) - failed} reports saved. "
                               "See failure details above.")
    elif args.command == "import":
        model_id = model_slug(args.model)
        target = workspace / "reports" / f"{model_id}.json"
        if target.exists():
            raise ValueError("A report for this model already exists")
        manifest = load_json(workspace / "manifest.json")
        report = parse_report(args.file.read_text(encoding="utf-8"),
                              {f["path"] for f in manifest["files"]},
                              review_modes=manifest.get("review_modes"))
        validate_locations(report, workspace / "snapshot")
        report["audit_metadata"] = {"model_id": model_id, "source": "manual",
                                     "commit": manifest["commit"]}
        save_json(target, report)
        print(f"Imported {model_id}: {len(report['findings'])} findings")
    elif args.command == "score":
        print(json.dumps(_data(workspace)["leaderboard"], indent=2))
    elif args.command in ("judge", "rubric"):
        model_id = model_slug(args.model)
        data = _data(workspace)
        report = data["reports"].get(model_id)
        if report is None:
            raise ValueError("No report for this model")
        path = workspace / "judgments" / f"{model_id}.json"
        decision = load_json(path) if path.exists() else {"findings": {}, "rubric": {}}
        if args.command == "judge":
            if "code_audit" not in data["review_modes"]:
                raise ValueError(
                    "Findings can only be adjudicated for code_audit reviews"
                )
            matches = [f for f in report["findings"] if f["id"] == args.finding]
            if not matches:
                raise ValueError("Unknown finding ID")
            if len(args.issue_key) > 100:
                raise ValueError("Issue key too long")
            decision.setdefault("findings", {})[args.finding] = {
                "verdict": args.verdict, "severity": args.severity or matches[0]["severity"],
                "issue_key": args.issue_key, "fabricated_evidence": args.fabricated_evidence,
            }
        else:
            maxima = {"improvements": 15, "gold": 20, "rigor": 5}
            rubric_fields = rubric_fields_for_modes(data["review_modes"])
            provided = {field: getattr(args, field) for field in maxima}
            unexpected = sorted(
                field for field, value in provided.items()
                if value is not None and field not in rubric_fields
            )
            if unexpected:
                raise ValueError(
                    "Rubric fields not selected for this review: " + ", ".join(unexpected)
                )
            missing = [field for field in rubric_fields if provided[field] is None]
            if missing:
                raise ValueError(
                    "Missing rubric scores for selected fields: " + ", ".join(missing)
                )
            scores = {field: provided[field] for field in rubric_fields}
            for field, value in scores.items():
                maximum = maxima[field]
                if not 0 <= value <= maximum:
                    raise ValueError("Rubric scores outside allowed range")
            existing_rubric = decision.get("rubric", {})
            if not isinstance(existing_rubric, dict):
                raise ValueError("Invalid rubric data")
            decision["rubric"] = {
                field: value for field, value in existing_rubric.items()
                if field in rubric_fields
            }
            decision["rubric"].update(scores)
        save_json(path, decision)
        print("Judgment saved")


def main() -> None:
    try:
        execute(parser().parse_args())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
