# Security Policy

## Supported code

Security fixes are evaluated against the current `main` branch and the latest
published stable release. Older releases may receive a fix only when the
maintainer explicitly confirms that a backport is practical.

## Reporting a vulnerability

Do not report suspected vulnerabilities in a public issue, discussion, pull
request, or commit message. Use this repository's
[private vulnerability reporting form](https://github.com/agentrof/agent-marketplace/security/advisories/new)
instead.

Include a minimal reproduction, the affected revision or release, the impact,
and any mitigations already tested. Do not include credentials, tokens, or
other sensitive production data in the report.

Maintainers will use the private advisory thread for follow-up, remediation,
credit preferences, and coordinated disclosure. Public disclosure happens only
after a fix or mitigation is available, unless the reporter and maintainer
agree on a different timeline.

## Scope

Reports are in scope when they affect the marketplace packages, generated host
distributions, repository automation, release process, or code executed by the
packaged workflow scripts. Dependency alerts and secret-scanning findings are
handled through GitHub's repository security alerts.
