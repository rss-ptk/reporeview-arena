# RepoReview Arena

**Independent multi-LLM audits of any Git repository. Evidence-first judging. One golden feature per contestant.**

RepoReview Arena turns a software repository into a reproducible code-audit competition:

1. Freeze the same Git commit as a source snapshot for every model.
2. Detect languages and generate a project-specific, evidence-first audit prompt.
3. Give each AI reviewer independent, **read-only repository navigation tools**.
4. Collect machine-readable reports with severities, file/line evidence, recommendations and one **golden feature**.
5. Adjudicate findings, avoid within-model duplicate scoring, penalize false positives and rank the contestants.
6. Review all results in a local interactive dashboard, with optional import of reports created in VS Code or another IDE.

This is an **MVP**, not a security certification, production trade gate or automatic proof of correctness. Human adjudication is required. **Unverified findings score zero.**

## Features

| Feature | Status |
| --- | --- |
| Local Git repository snapshot, commit SHA, language fingerprint, exclusion manifest | Implemented |
| Generic audit prompt, hotpath focus, read-only navigation | Implemented |
| BYOK multi-provider model orchestration via LiteLLM | Implemented (credentials required) |
| Independent conversations, bounded parallelism and tool steps | Implemented |
| JSON reports, manual import from coding agents/VS Code | Implemented |
| Evidence-first scoring, severity override, canonical issue dedup within each report | Implemented |
| Local browser review, golden feature scores and ranking | Implemented |
| Automated bug reproduction, secure code execution sandbox | **Not implemented** |
| Automatic cross-model semantic dedup, adversarial reviewer round | **Planned** |
| GitHub installation / hosted SaaS / GitHub Actions commenting | **Not implemented** |
| Dollar-denominated cost ceiling, cross-provider effort parity | **Not implemented** |

## Setup

Python 3.11+ and Git. No Docker required.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[llm,dev]'
reporeview --help
```

For a fully offline/manual flow, install without the LLM extra: `pip install -e .`.

## Create a competition

First clone the target project **locally** and check out the commit to audit. `reporeview init` accepts a local Git checkout root, not arbitrary websites. For private repositories, this avoids automatically exposing credentials or cloning private source to an unknown workspace.

```bash
git clone https://github.com/owner/project.git
cd project
git rev-parse HEAD   # save this SHA
cd ..
reporeview init --repo ./project --out ./arenas/project-2026 --focus "Audit critical hotpaths, concurrency, accounting, and reliability"
```

The command creates:

```text
arenas/project-2026/
  manifest.json      # Git SHA, included/excluded tracked files and hashes
  prompt.md          # reusable identical prompt for all competing reviewers
  snapshot/          # fixed sanitized-ish read-only source COPY
  reports/           # generated automatically once reports arrive
  judgments/         # manual validation and rubric scores
```

**Important:** the snapshot excludes common sensitive paths and file extensions; this is **not secret detection**. Review `snapshot/` before authorizing external AI calls. Never assume source code is safe to upload solely because the automatic denylist accepted it. Untracked files are not included; modified tracked files make initialization fail. Large and binary files are omitted and recorded in `manifest.json`.

## Option A: audit using APIs (Bring Your Own Keys)

```bash
cp models.example.json models.json
# Edit provider/model IDs to versions available in YOUR provider account.
# Export API keys into your environment, e.g. via your secrets manager.
# NEVER add API keys to models.json or commit them.
reporeview run --workspace ./arenas/project-2026 --models models.json --concurrency 2 --max-steps 35 --confirm-send-code
```

Models are addressed by LiteLLM's provider/model identifiers. The example contains **intentional placeholders**, not assertions that a specific model release is available. OpenAI, Anthropic, Gemini, Mistral, xAI and OpenRouter are example routes; GLM/Qwen/Kimi/MiMo may require OpenRouter or a compatible provider account. Refer to provider documentation for exact current IDs and key environment variables.

Each model gets an isolated conversation and only three functions: `list_files`, `read_file` and `search_code`. It cannot ask the Arena to execute code, write to the target repo, run shell commands or issue network requests. These restrictions apply to Arena tools; the upstream model provider receives the source excerpts it requests.

Runs save `reports/<model-id>.json`. The CLI refuses to overwrite them. A failed model is reported separately, without corrupting successful reports. Token usage is recorded when returned by the provider; cost billing depends on the provider and is not capped by this version.

## Option B: audits via VS Code or other coding agents

```bash
reporeview prompt --workspace ./arenas/project-2026 > shared-prompt.md
```

Open the same frozen repository content for each model, use the same prompt, and ask for the final structured JSON deliverable. When manual reviewers have wider tools (terminals, patches, internet), the methodology is *not strictly equivalent* to the Arena's API mode. Disclose differences in the reports.

Import the JSON or a Markdown fenced `json` block:

```bash
reporeview import --workspace ./arenas/project-2026 --model claude --file claude-report.json
reporeview import --workspace ./arenas/project-2026 --model gemini --file gemini-report.md
```

All finding paths must refer to approved snapshot files. Missing / malformed mandatory fields are rejected. Import accepts only structured JSON, not unstructured prose or arbitrary Markdown.

## Adjudicate, rank and compare features

```bash
reporeview web --workspace ./arenas/project-2026
# Open http://127.0.0.1:8765
```

The browser presents reports, golden-feature proposals, per-finding decisions and scoring rubrics. Imported reports can also be added directly through the dashboard. Scores persist under `judgments/`.

CLI equivalents:

```bash
reporeview judge --workspace ./arenas/project-2026 --model claude --finding FS-AUD-001 --verdict confirmed --severity P1 --issue-key EDGE-001
reporeview rubric --workspace ./arenas/project-2026 --model claude --improvements 12 --gold 17 --rigor 4
reporeview score --workspace ./arenas/project-2026
```

Verdicts: `pending`, `confirmed`, `false_positive`. When a model reports multiple symptoms of the same root cause, assign the same `--issue-key` to count only once **within that model**. The same valid finding discovered independently by several models earns points for each of those models. Severity is set by the adjudicator, and false positives cost points regardless of their original severity.

### Scoring (100 points)

| Category | Maximum | Rules |
| --- | ---: | --- |
| Confirmed bugs | 60 | P0: 20, P1: 10, P2: 4, P3: 1 |
| General improvements | 15 | Human-assessed |
| Golden feature | 20 | Human-assessed: value, falsifiable test, novelty, feasibility |
| Rigor and coverage | 5 | Human-assessed |
| False positives | Penalty | -5 each; -10 when evidence is knowingly fabricated |

Totals are clamped to the interval [0, 100]. A model's output does not prove its own findings. Until validated, zero points are assigned for its reported defects. This prevents raw finding count from deciding the competition.

Golden feature evaluation should require an actual gap check, implementation path, testable value hypothesis, and transparent limitations. Demonstrated value should outperform unsupported projections.

## Security and scope

- **Local-by-default:** browser serves only on `127.0.0.1`, not the public internet. This is not a multi-user authenticated web app.
- **Read-only source tools:** model calls can list/search/read only the frozen snapshot. No shell, tests, arbitrary URLs or filesystem writes are exposed to reviewers.
- **Prompt injection:** repository files are treated as untrusted input. The instruction asks models not to follow embedded instructions, but LLM prompt injection defenses are not guaranteed.
- **Sensitive files:** automatic exclusions are best-effort. Review every snapshot before sending any fragments to external providers. Sensitive information may appear in ordinary source files, comments or generated reports.
- **Privacy:** models receive code excerpts through your configured providers. Review each provider's policies and licensing before analyzing proprietary work.
- **Expenses:** tool budgets bound the number of model interactions, not guaranteed spend. Avoid premium models until you understand per-call pricing.
- **No code execution:** reproductions and tests proposed by models are not executed by the Arena. Human reviewers must validate them in an isolated environment outside the app.
- **No execution parity guarantee:** different models have different tool-calling, context and reasoning capabilities. For comparisons, document exact version, model ID, provider, settings and duration.

## Suggested project roadmap

- [ ] V0.2: adversarial second-pass adjudication, cross-model semantic clustering with explicit reviewer approval, automatic regression-test PR suggestions.
- [ ] V0.3: stronger snapshot controls (detect secrets, custom allow/deny rules), SHA-pinned GitHub URLs, token/cost budgets, pricing exports.
- [ ] V0.4: optional secure test execution via an isolated runner, offline fixture reproduction, CI report integration.
- [ ] V1.0: plugins for IDE coding agents / MCP, configurable competition rubrics, reproducibility export bundles, shared benchmark fixtures.

## Contributing

Contributions, language adapters, provider compatibility fixes, test fixtures and documentation are welcome. Please open an issue before implementing large changes. Do not include private repositories, credentials or sensitive audit results in issues or PRs.

MIT licensed.
