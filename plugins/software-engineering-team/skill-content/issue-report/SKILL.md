---
name: issue-report
description: Capture and explicitly approve a Software Engineering Team issue as a tracked Markdown report, with optional filing to the marketplace repository.
exposure: entry
---

# Issue Report

Load the `obsidian-vault` skill before creating or editing an issue report.

Issues and their approval status are ordinary tracked Markdown under
`workspace/docs/issues/`.

## When to Use

- A defect, improvement or repository problem needs a durable report.
- The owner wants to review an issue before filing it upstream.

## Procedure

1. Create the schema-valid stub with its direct title and graph links:

   ```sh
   scripts/issue_compile.py init --docs workspace/docs \
     --slug short-problem --title "Short problem" \
     --kind defect --id ISSUE-001
   ```

2. Replace every stub prompt with reproduction or motivation, expected and
   actual behavior, impact and justified severity, evidence, and a proposed
   next action. Keep secrets and raw oversized logs out of the report.
3. Run `scripts/issue_compile.py check --docs workspace/docs --render`. Resolve
   every finding, then show the complete diff to the owner. After explicit
   approval run `scripts/issue_compile.py approve --report <path>`; do not edit
   an approved report without returning it to draft and approving it again.
4. Only when the owner separately and explicitly requests external filing, run
   `scripts/file_issue.py --report <path> --dry-run`, then repeat without
   `--dry-run`. The filer rejects draft or hash-stale reports, posts only to the
   fixed `agentrof/agent-marketplace` target and records the returned URL and
   `filed` status in the report.

Issue reporting is part of the Software Engineering Team and remains separate
from delivery execution.
