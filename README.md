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
| Generic audit prompt with weighted review criteria and custom focus, read-only navigation | Implemented |
| BYOK multi-provider model orchestration via LiteLLM | Implemented (credentials required) |
| Independent conversations, bounded parallelism and tool steps | Implemented |
| JSON reports, manual import from coding agents/VS Code | Implemented |
| Evidence-first scoring, severity override, canonical issue dedup within each report | Implemented |
| Local browser review, golden feature scores and ranking | Implemented |
| Five-step dashboard workflow with review priorities and guided adjudication | Implemented |
| Saved repository selection and per-repository review history in the Control Page | Implemented |
| Dashboard contestant selection, progress reporting and partial-failure handling | Implemented |
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
  failures/          # diagnostics for rejected or incomplete model reports
```

**Important:** the snapshot excludes common sensitive paths and file extensions; this is **not secret detection**. Review `snapshot/` before authorizing external AI calls. Never assume source code is safe to upload solely because the automatic denylist accepted it. Untracked files are not included; modified tracked files make initialization fail. Large and binary files are omitted and recorded in `manifest.json`.

## Option A: audit using APIs (Bring Your Own Keys)

```bash
cp models.example.json models.json
# Edit provider/model IDs to versions available in YOUR provider account.
# Export API keys into your environment or put them in an untracked .env in this directory.
# reporeview run/web loads .env at startup; existing environment variables take precedence.
# NEVER add API keys to models.json or commit them.
reporeview run --workspace ./arenas/project-2026 --models models.json --concurrency 2 --max-steps 35 --confirm-send-code
```

Models are addressed by LiteLLM's provider/model identifiers. The example contains **intentional placeholders**, not assertions that a specific model release is available. OpenAI, Anthropic, Gemini, Mistral, xAI and OpenRouter are example routes; GLM/Qwen/Kimi/MiMo may require OpenRouter or a compatible provider account. Refer to provider documentation for exact current IDs and key environment variables.

OpenRouter entries can add a `provider` block to control which upstream provider serves the model, for example `"provider": {"sort": "price", "quantizations": ["fp8", "bf16", "fp16"], "max_price": {"prompt": 1, "completion": 4}}`. Only these three fields are accepted. `sort` (`price`, `throughput` or `latency`) picks the preferred provider; `quantizations` drops endpoints at other precisions, such as low-precision `int4`, but set it per model: a natively quantized model has no endpoints left if you exclude its own precision. `max_price` (USD per million tokens) makes the request fail instead of running on a more expensive provider. `provider.order` is rejected because OpenRouter does not use sticky routing with a manual order, so every tool round could land on a different provider and lose its prompt cache. `sort: "price"` keeps choosing the same cheapest provider; `throughput` and `latency` follow live metrics and may switch providers between rounds. The block is refused for non-OpenRouter models and for `api_base` values other than OpenRouter.

OpenRouter entries can also add a `reasoning` block to limit reasoning tokens, which are billed as output and count toward the output limit. Set exactly one field: `"reasoning": {"effort": "low"}` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`) or `"reasoning": {"max_tokens": 8000}` for a fixed budget. For budget-based models OpenRouter turns `effort` into a share of `max_tokens`, so pair it with a per-model `max_tokens` when you need a predictable cap. When both `max_tokens` values are set, `reasoning.max_tokens` must be lower than the model's `max_tokens`; otherwise reasoning could use the whole output budget and leave an empty report. The same block is sent on every call, the final report included, because changing reasoning settings between rounds can cost a prompt cache miss. Models without reasoning support may ignore it, and `effort: "none"` disables reasoning only where the model allows it.

Each model gets an isolated conversation and only three functions: `list_files`, `read_file` and `search_code`. It cannot ask the Arena to execute code, write to the target repo, run shell commands or issue network requests. These restrictions apply to Arena tools; the upstream model provider receives the source excerpts it requests.

Runs save `reports/<model-id>.json`. The CLI refuses to overwrite them. A failed model is reported separately, without corrupting successful reports. Token usage is recorded when returned by the provider; cost billing depends on the provider and is not capped by this version.

Temporary provider failures (rate limits, service outages, connection errors and timeouts) retry up to twice per API call, with delays starting at five seconds and doubling up to 60 seconds. `--retries 0` disables retries; `--retry-delay` sets the initial delay. The Arena honors `Retry-After`; a provider asking for more than 60 seconds ends that audit with the requested wait in the failure details. Retries keep the same model and conversation.

For free endpoints, start with one concurrent auditor to reduce request bursts:

```bash
reporeview run --workspace ./arenas/project-2026 --models models.json --concurrency 1 --retries 2 --retry-delay 5 --confirm-send-code
```

Each tool round is another API request. After the last round the Arena makes one final call for the report; it keeps the same tool definitions with `tool_choice: "none"`, so the request still matches the cached prompt prefix. If a provider ignores `tool_choice` and requests tools anyway, the Arena repeats the final call once without tools, which adds one uncached request. Reducing concurrency does not reset daily quotas or restore an unavailable model. Check [OpenRouter rate limits](https://openrouter.ai/docs/api/reference/limits) and [provider errors](https://openrouter.ai/docs/api/reference/errors) when these failures persist.

Failures include a `stage` (`provider`, `report` or `workspace`) and an actionable message. Provider failures include an HTTP status and `Retry-After` when available; Arena diagnostics omit SDK exception messages and request payloads. Invalid, empty or truncated model reports are saved locally to `failures/<model-id>.json` with `raw_response`, `validation_error`, `finish_reason`, any configured `max_tokens` value, last-call token usage and aggregate token usage. Inspect this file to distinguish malformed JSON, schema problems and invalid file/line citations. The latest rejected response replaces an older failure artifact for the same model; it is never accepted as a scored report. The Arena does not set a default output-token limit. To set one for a specific model, add `max_tokens` to that model's `models.json` entry. If a provider truncates output, set a suitable per-model limit or request a shorter report. To rerun only failed contestants, set `"enabled": false` on entries with existing successful reports. In the dashboard this is automatic: contestants with a saved report are locked and only the rest can be ticked.

## Option B: audits via VS Code or other coding agents

```bash
reporeview prompt --workspace ./arenas/project-2026 > shared-prompt.md
```

Open the same frozen repository content for each model, use the same prompt, and ask for the final structured JSON deliverable. When manual reviewers have wider tools (terminals, patches, internet), the methodology is *not strictly equivalent* to the Arena's API mode. Disclose differences in the reports.

### Usage and cost monitoring

Each provider attempt is recorded in `usage/<run-id>/<model-id>/`, including failed and interrupted attempts. The dashboard displays per-run, per-model and per-attempt token, cache and OpenRouter cost data; the same history is available as JSON:

```bash
reporeview usage --workspace ./arenas/project-2026
reporeview usage --workspace ./arenas/project-2026 --run-id <run-id>
```

Amounts are the OpenRouter credits reported by responses, not a complete invoice. A partial or unavailable total means at least one attempt has no reported cost; zero is shown only when actually reported. Provider cost, when available, is separate, including for BYOK. Token counts, cache and reasoning fields may also be unavailable. Imported and older reports cannot supply historical costs. The usage records contain metadata only, not prompts, source code or model responses. A failed attempt also records its error class and HTTP status when available, such as `RateLimitError` with 429 or `NotFoundError` with 404, and the dashboard shows them in the attempt list; SDK error messages are never stored. Monitoring does not impose a budget or query OpenRouter for missing data.

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

When `models.json` exists in the current directory, `reporeview web` loads its enabled models automatically. Pass `--models <path>` to use a different file; without either file, provider audits stay disabled while manual import remains available. The CLI loads provider keys from the process environment or an untracked `.env` in the current directory, with existing environment variables taking precedence. The dashboard can also be started without `--workspace`. In the Control Page, choose and save the root of one local Git checkout, then create a review or open one from that repository's history. The browser does not ask where review files should go: each review gets a unique folder under the app's fixed per-user data directory, so switching repositories keeps their histories separate. The only configurable path in the Control Page is the checkout root.

The fixed data directory follows the operating system's standard per-user location:

| System | Data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\RepoReviewArena` |
| macOS | `~/Library/Application Support/RepoReviewArena` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/reporeview-arena` |

It contains `settings.json` with only the absolute `repository_path` and a `reviews/<id>/` folder for each Control Page review. The review folder contains its manifest, snapshot, prompt, reports, judgments and any model failure diagnostics. Existing command workflows that use `--workspace` and `--out` continue to work with their chosen folders; they are not moved into the managed history automatically. New Control Page reviews default to both outputs, but you can select **Code audit**, **Golden Feature**, or both. Code audit settings include nine review priorities (0-5) and optional custom criteria; Golden Feature settings select proposal directions such as architecture, integrations, user-facing functionality, developer experience, security, performance, reliability, or data and reporting. If both are selected, each model returns the audit and proposal in separate sections of one report. The page collects judgments and rubric scores for the selected outputs and shows separate scoreboards: code audit is scored out of 80, and Golden Feature out of 20. Reviews created before mode selection are treated as both. `reporeview init` continues to create both outputs by default. The panel can start provider runs, show per-model progress and partial failures, and its **Models for this review** checkbox group lists enabled models from `models.json`; select contestants for a run (Select all and Clear selection are provided). A model with a report is locked so results are never overwritten, and models added later start unticked. The panel can also display/copy/download the generated prompt, import reports, adjudicate findings, set rubric scores and download score JSON. Each provider run requires fresh confirmation before source fragments are sent. Provider credentials remain in the local CLI process environment. `--models` is optional; without it, manual review and import remain available while provider runs are disabled. Scores persist under `judgments/`.

CLI equivalents:

```bash
reporeview judge --workspace ./arenas/project-2026 --model claude --finding FS-AUD-001 --verdict confirmed --severity P1 --issue-key EDGE-001
reporeview rubric --workspace ./arenas/project-2026 --model claude --improvements 16 --gold 17
reporeview score --workspace ./arenas/project-2026
```

Verdicts: `pending`, `confirmed`, `false_positive`. When a model reports multiple symptoms of the same root cause, assign the same `--issue-key` to count only once **within that model**. The same valid finding discovered independently by several models earns points for each of those models. Severity is set by the adjudicator, and false positives cost points regardless of their original severity.

The `rubric` command requires only the flags for the workspace's selected modes: `--improvements` for Code audit (0-20), and `--gold` for Golden Feature (0-20). It rejects scores for unselected modes. `judge` is available only when Code audit is selected. Workspaces without `review_modes` retain the legacy default of both modes.

### Scoring (100 points)

| Category | Maximum | Rules |
| --- | ---: | --- |
| Confirmed bugs | 60 | P0: 20, P1: 10, P2: 4, P3: 1 |
| General improvements | 20 | Human-assessed |
| Golden feature | 20 | Human-assessed: value, falsifiable test, novelty, feasibility |
| False positives | Penalty | -5 each; -10 when evidence is knowingly fabricated |

Totals are clamped to the interval [0, 100]. A model's output does not prove its own findings. Until validated, zero points are assigned for its reported defects. This prevents raw finding count from deciding the competition.

The Control Page presents two independent rankings instead of combining these categories: code audit ranks confirmed bugs and general improvements (up to 80 points, less false-positive penalties); Golden Feature ranks the proposal score (up to 20 points). The CLI combines selected categories, so its 100-point score remains available when both modes are selected. Existing saved Rigor scores are ignored and are not transferred to Improvements; old judgment files are left untouched until scores are saved again.

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
