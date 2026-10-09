# Security Policy

This is an early-stage local-first tool. Do not expose the dashboard to an
untrusted network. The dashboard binds to 127.0.0.1 and checks the Host header,
origin, and a per-process write token. It is **not** designed as a multi-user
service and should not be deployed behind a public reverse proxy.

## Sensitive source handling

The snapshot scanner excludes common secret filenames and binary/large
artifacts, but cannot guarantee that accepted source does not contain secrets.
Manually inspect snapshots before granting API access. External LLM providers
may retain code fragments according to their own data practices.

## Reporting vulnerabilities

Please use GitHub's private vulnerability reporting feature for the repository,
when enabled. Otherwise, contact maintainers privately rather than posting
exploitable details or private data in a public issue.

Do not submit raw model responses that contain confidential source code.
