"""Command line UX for initialization, running, import, scoring and web review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .app import _data, serve
from .orchestrator import audit_many, model_slug
from .prompts import build_prompt
from .reports import parse_report, validate_locations
from .repository import RepositoryReader, snapshot_repo
from .storage import load_json, save_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="reporeview", description="Multi-LLM code audit arena")
    command = root.add_subparsers(dest="command", required=True)
    init = command.add_parser("init", help="Create a frozen audit workspace for a clean Git checkout")
    init.add_argument("--repo", required=True, type=Path)
    init.add_argument("--out", required=True, type=Path)
    init.add_argument("--focus", default="Audit the most consequential hotpaths, correctness, failure recovery, security, and major architecture gaps. Focus on what can cause real harm.")
    audit = command.add_parser("run", help="Audit concurrently using configured BYOK models")
    audit.add_argument("--workspace", required=True, type=Path)
    audit.add_argument("--models", required=True, type=Path, help="Path to models.json")
    audit.add_argument("--concurrency", type=int, default=2)
    audit.add_argument("--max-steps", type=int, default=35)
    audit.add_argument("--confirm-send-code", action="store_true", help="Explicitly authorize selected providers to receive approved source fragments")
    imp = command.add_parser("import", help="Import a structured JSON report from VS Code or elsewhere")
    imp.add_argument("--workspace", required=True, type=Path)
    imp.add_argument("--model", required=True)
    imp.add_argument("--file", required=True, type=Path)
    web = command.add_parser("web", help="Launch a local adjudication and ranking dashboard")
    web.add_argument("--workspace", required=True, type=Path)
    web.add_argument("--port", type=int, default=8765)
    score = command.add_parser("score", help="Output current validated scorecard as JSON")
    score.add_argument("--workspace", required=True, type=Path)
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
    rubric.add_argument("--improvements", type=int, required=True)
    rubric.add_argument("--gold", type=int, required=True)
    rubric.add_argument("--rigor", type=int, required=True)
    return root


def execute(args: argparse.Namespace) -> None:
    if args.command == "init":
        manifest = snapshot_repo(args.repo, args.out)
        save_json(args.out / "manifest.json", manifest)
        reader = RepositoryReader(args.out / "snapshot")
        text = build_prompt(manifest, args.focus, reader.describe())
        (args.out / "prompt.md").write_text(text, encoding="utf-8")
        print(json.dumps({"workspace": str(args.out), "commit": manifest["commit"],
                          "files": manifest["file_count"], "skipped": len(manifest["skipped"])}, indent=2))
        print("Review manifest.json excluded files. The snapshot is for external LLM sharing.")
        return
    workspace = args.workspace.resolve()
    if not (workspace / "manifest.json").exists():
        raise ValueError("Workspace not initialized; run 'reporeview init' first")
    if args.command == "prompt":
        print((workspace / "prompt.md").read_text(encoding="utf-8"))
    elif args.command == "run":
        if not args.confirm_send_code:
            raise ValueError("Sending source requires --confirm-send-code")
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
        print(f"Sending read-only snapshot fragments to {len(approved)} model providers.")
        outcome = asyncio.run(audit_many(workspace, approved, concurrency=args.concurrency,
                                        max_steps=args.max_steps))
        print(json.dumps(outcome, indent=2))
        if any("error" in item for item in outcome):
            raise RuntimeError("Some model audits failed; successful reports were saved")
    elif args.command == "import":
        model_id = model_slug(args.model)
        target = workspace / "reports" / f"{model_id}.json"
        if target.exists():
            raise ValueError("A report for this model already exists")
        manifest = load_json(workspace / "manifest.json")
        report = parse_report(args.file.read_text(encoding="utf-8"),
                              {f["path"] for f in manifest["files"]})
        validate_locations(report, workspace / "snapshot")
        report["audit_metadata"] = {"model_id": model_id, "source": "manual",
                                     "commit": manifest["commit"]}
        save_json(target, report)
        print(f"Imported {model_id}: {len(report['findings'])} findings")
    elif args.command == "web":
        serve(workspace, port=args.port)
    elif args.command == "score":
        print(json.dumps(_data(workspace)["leaderboard"], indent=2))
    elif args.command in ("judge", "rubric"):
        model_id = model_slug(args.model)
        report = _data(workspace)["reports"].get(model_id)
        if report is None:
            raise ValueError("No report for this model")
        path = workspace / "judgments" / f"{model_id}.json"
        decision = load_json(path) if path.exists() else {"findings": {}, "rubric": {}}
        if args.command == "judge":
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
            for value, maximum in ((args.improvements, 15), (args.gold, 20), (args.rigor, 5)):
                if not 0 <= value <= maximum:
                    raise ValueError("Rubric scores outside allowed range")
            decision["rubric"] = {"improvements": args.improvements, "gold": args.gold,
                                  "rigor": args.rigor}
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
