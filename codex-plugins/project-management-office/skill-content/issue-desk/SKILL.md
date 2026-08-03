---
name: issue-desk
description: Reviews captured issue candidates and files the owner-approved ones to the Agentrof marketplace's own repository. The draft-and-approve desk for marketplace defect and improvement reports.
disable-model-invocation: true
---

# Issue Desk

Issue candidates (marketplace defects and improvements surfaced while the
team works) rest in the central database until the owner reviews them here
and approves filing. Filing targets one repository only: the Agentrof agent
marketplace's own tracker. Nothing is ever posted without explicit
per-candidate owner approval; the candidate list is the default resting state.

## Capturing candidates

Candidates enter the list during any run, not only from this desk. When a
persona or flow surfaces a marketplace defect or improvement in its reply,
the orchestrator records it with one CLI call, the same way review findings
are recorded: `"$PMO" issue open --title "<one line>" --kind
defect|improvement` with optional `--body`, `--evidence` and
`--work-order-key`. Agents never write the database themselves (the guard
denies it); capture is always the orchestrator recording what an agent
surfaced. Recorded candidates wait here until the owner reviews and files.

## When to Use

- The owner wants to review captured issue candidates or file approved ones.
- A run surfaced a marketplace defect or improvement worth reporting and the
  owner asks to send it upstream.

## Procedure

1. Resolve the CLI: prefer the synced launcher
   `PMO="${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py"`. If it is missing,
   find this plugin's installed scripts/pmo_cli.py and run its `ensure`
   subcommand once (it syncs the launcher and file_issue.py beside it), then
   use `"$PMO"`.
2. List the open candidates: `"$PMO" issue list --status candidate --json`.
   Present each to the owner: external id, kind, title, body draft, evidence.
   If none are open, say so and stop.
3. For each candidate the owner wants to act on, take exactly one decision:
   - Edit before filing: `"$PMO" issue update --issue <IC>` with any of
     `--title`, `--body`, `--evidence`.
   - Drop it: `"$PMO" issue update --issue <IC> --status dismissed`.
   - Approve for filing: continue to step 4.
   Never batch-approve; the owner approves each candidate individually.
4. File an approved candidate. Resolve the filer beside the launcher,
   `FILE="${AGENTROF_HOME:-$HOME/.agentrof}/bin/file_issue.py"` (or this
   plugin's scripts/file_issue.py). Write the approved body to a temp file
   and run `"$FILE" --title "<title>" --body-file <path>`. The target
   repository is locked inside file_issue.py and cannot be redirected; it
   prints the created issue URL on success.
5. Record the outcome so the candidate leaves the open set and the dashboard
   reflects it: `"$PMO" issue file --issue <IC> --url <printed-url>`.
6. Credentials: file_issue.py prefers the `gh` CLI, else a token from
   GH_TOKEN, GITHUB_TOKEN or `gh auth token`. With neither it posts nothing
   and reports the missing-credential path; relay that to the owner and stop.

## Failure Reporting

- CLI or filer missing at both the launcher and the plugin copy: the
  project-management-office plugin files are absent; reinstall it from the
  marketplace.
- file_issue.py refuses with a manifest-mismatch message: the installed
  marketplace manifest declares a repository other than the locked target;
  do not attempt to file, report the mismatch.
