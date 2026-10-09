# Publishing the public GitHub repository

The GitHub creation/push step requires a GitHub account authenticated with
GitHub CLI (`gh`). Check and review the local source tree **before publishing**.

```bash
# Run from the extracted reporeview-arena/ directory.
git init -b main
git add .
git commit -m "Initial public MVP: multi-LLM audit arena"
gh auth login
gh repo create reporeview-arena --public --source=. --remote=origin --push
```

Change the repository name if it is not available. To publish to a specific
GitHub owner, supply `OWNER/reporeview-arena` instead of `reporeview-arena`.

The source ZIP deliberately contains no `.git` directory, credentials or LLM
response artifacts. Review `.env.example` and the source policy before
publishing. The optional demo ZIP is a separate illustrative, fully local
competition, not an actual model benchmark.
