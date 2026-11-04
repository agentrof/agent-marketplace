# Security Policy

## Supported code

Security fixes are evaluated against the current `main` branch and the latest
published stable release. Older releases may receive a fix only when the
maintainer explicitly confirms that a backport is practical.

## Report a vulnerability privately

Use the repository's
[private vulnerability reporting form](https://github.com/agentrof/agent-marketplace/security/advisories/new).
Keep suspected vulnerabilities out of public issues, discussions, pull requests
and commit messages.

Include the minimum evidence needed to investigate.

- Affected package revision or release, host, CLI version and operating system.
- The operation, required permissions and relevant hook or sandbox state.
- A minimal reproduction using dummy data and an isolated local environment.
- Expected and observed behavior, the affected resource and security impact.
- Any mitigation already tested.

Remove credentials, tokens, private project identities and production data from
the report. Describe the needed configuration with sanitized values. Routine
installation and workflow questions follow [SUPPORT.md](SUPPORT.md).

## Scope and trust boundaries

In-scope reports affect canonical skills and scripts, generated distributions,
host adapters and hooks, project setup and migration, local coordination and
proof records, repository automation or release integrity.

The team can execute project commands and operate GitHub under the authority
available to its host. Its security boundaries include the following.

- Host permissions and sandbox controls govern process and filesystem access.
  Hook checks apply to supported operations only while those hooks are active.
- Worker packets supply scoped instructions and sources. Their receipts establish
  preparation or recorded use, while semantic model compliance needs separate
  verification.
- Local coordination and evidence checks protect cooperating workflow operations.
  A process with the same filesystem authority can alter local state. Local
  receipts do not create new authorization.
- Issue text, project artifacts and command output supply evidence. They cannot
  expand a grant or authorize publication, destructive actions or a release.
- Companions have their own publishers, installation scope and behavior. Report an
  integration defect here. Report a defect confined to a companion through its
  publisher's security process.

When reporting a bypass, identify the intended boundary, the starting authority
and the additional action or data access achieved. A reproducible path from
untrusted content to an unauthorized operation is relevant even if a prompt
instructed the model to refuse it. [Architecture and enforcement scope](docs/architecture.md)

## Investigation and disclosure

Use only systems, repositories and data you are authorized to test. Prefer the
smallest local reproduction. Avoid production data access, service disruption
and publishing exploit details while remediation is in progress.

Maintainers use the private advisory thread for follow-up, remediation, credit
preferences and coordinated disclosure. Public disclosure happens after a fix
or mitigation is available, unless the reporter and maintainer agree on a
different timeline. Dependency alerts and secret-scanning findings are handled
through GitHub's repository security alerts.
