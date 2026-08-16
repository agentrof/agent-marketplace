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

1. Create `workspace/docs/issues/<slug>.md` with front matter:

   ```yaml
   ---
   type: issue-report
   status: draft
   kind: defect
   title: Short report title
   owner_role: product_owner
   ---
   ```

2. Record reproduction/evidence, expected and actual behavior, severity,
   affected files, and a proposed next action. Keep the report in Git and ask
   the owner to change `status` to `approved` before any external filing.
3. For an approved report, run
   `scripts/file_issue.py --title "..." --body-file <path>` only after the
   owner explicitly requests filing. Use `--dry-run` to verify the fixed
   `agentrof/agent-marketplace` target without posting.
4. Add the returned URL and set `status: filed` in the same tracked change.

Issue reporting is part of the Software Engineering Team and remains separate
from delivery execution.
