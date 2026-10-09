# Contributing

RepoReview Arena is an evidence-first audit tool. Contributions must preserve
independent review, source confidentiality and fair scoring.

1. Open an issue describing the scenario and intended user benefit.
2. Keep modules small and deterministic. Avoid adding dependencies without a clear reason.
3. Add offline unit tests for every change in audit semantics or scoring.
4. Run `python -m pip install -e '.[dev]'`, `ruff check .`, and `pytest -q`.
5. Never include private repositories, keys, production logs, or API responses
   containing user source code in issues, commits or test fixtures.
6. No shell/terminal or network tools for auditing agents without a deliberately
   designed sandbox and explicit authorization model.

## Contribution areas

- Additional model/provider integration and compatibility tests
- Robust, reproducible adjudication workflows
- Accurate cross-model semantic deduplication with human approval
- Safe isolated reproductions and property-based tests
- Support for more languages, monorepos and large repositories
- Golden-feature validation frameworks and cost observability

## Release readiness

This is an MVP. Public API stability, full CLI/browser workflow tests and a
security review are required before claiming production-readiness.
