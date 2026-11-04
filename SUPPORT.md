# Support

For installation and first use, start with the [README](README.md#quick-start).
For project refresh or migration, read the [upgrade protocol](docs/upgrade-protocol.md).
The [orchestration guide](docs/orchestration.md) explains delivery, workers and loops.
Plugin authors should also read the [authoring guide](docs/authoring.md).

## Report a problem

Use the [issue forms](https://github.com/agentrof/agent-marketplace/issues/new/choose)
for reproducible defects, workflow questions and improvement proposals. Include
only the information needed to investigate.

- Host and CLI version, operating system, Python version and plugin version.
- The operation attempted, expected result and actual result.
- A minimal anonymized reproduction and relevant error excerpt.
- Whether the host was reloaded and whether required hooks were active.

The team's `issue-report` entry can help diagnose a plugin defect, verify an
isolated local repair candidate and prepare an anonymized report. Publication
requires explicit authority. Review the payload before publishing.

Keep credentials, private project names, paths, content and identifying links
out of public reports. Report vulnerabilities through the
[private vulnerability form](https://github.com/agentrof/agent-marketplace/security/advisories/new)
and follow [SECURITY.md](SECURITY.md).
