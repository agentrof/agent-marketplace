# Project context resolution

`project_context.py` prepares a bounded reading plan from references that an
entry has already selected. It returns source paths, record or section
addresses, reasons and byte sizes. It never chooses skills, changes a source,
executes an environment command or creates approval authority.

## Workflow

1. Resolve the entry's scope normally. Supply its exact record references to
   `project_context.py --project-root <root> resolve --entry <entry> --role <role>
   --ref <reference>`, repeating `--ref` for the selected inputs.
2. Inspect `must_read`, `coverage` and `optional_groups`. The initial plan
   includes the selected sources and their required outgoing constraints, not
   every descendant of a parent epic or every incoming citation. The separate
   impact closure continues to judge the affected review scope.
3. Save the JSON in the project's runtime scratch and pass that project-relative
   path to `task_inputs.py --context-plan <path>`. The task validates the plan's
   entry, role, source membership and snapshot and binds its source files.
4. `project_context.py --project-root <root> read --plan <path>` reads the
   selected source text in one call. A row retains its table header. Generated
   inverse relations and navigation are not duplicated into authored text.
5. A `needs_split` plan has required work left. `units --ref <source>` lists
   smaller source units in pages. `expand --plan <path> --reason <reason>` takes
   the next page; repeated `--ref` adds explicitly requested context. A single
   oversized unit requires selecting its smaller units or explicitly raising
   the budget. No required unit is silently truncated or dropped.
6. Recheck with `check --plan <path>` before handoff. A changed source, removed
   source, new reference identity, changed graph or changed policy invalidates
   the plan. A new reader still receives the source content it needs; a hash is
   not evidence that the reader knows or reviewed that content.

## Policy and source types

`templates/project-context-policy.json` declares entry profiles, role
preferences, required versus optional relation keys, supported purposes,
source roots and separate limits for initial file count, source bytes and
metadata bytes. It contains no project-specific paths or topic vocabulary.
The existing task policy validates which roles belong to each entry.

`context_catalog.py` uses the compiler's Markdown parser. It addresses qualified
BA records, scenarios, current component/architecture identities, stable block
IDs and exact architecture or Experience ledger receipts. Ambiguous bare IDs
are refused; callers qualify them or use an exact path. Section addresses are
internal selectors tied to a source hash, not new heading wikilinks in the vault.
Historical receipts retain their exact revision and are never replaced by a
current document. Hash-pinned Operation, Requirement and Backlog sources are
resolved from Git history when the current source differs, with the owning
compiler's semantic digest recomputed before use. Resolving a receipt is not proof that an owning compiler
accepts it for a new handoff; those gates still run.

Explicit references inside `workspace/memory` and `workspace/environment` use
the declared source adapters. Owner context and operational evidence retain
distinct authority labels. They are never searched across other projects or
allowed to overwrite an approved binding. Skill and flow files are outside
these adapters. The existing optional `context_pack: role_digest` feature is
independent of this project-source reading plan.

## Approval baselines and quality

`task-input-policy.json` declares approval anchors for each document workflow.
Scoped packages use their own anchors; a task spanning several packages uses
the oldest required approval event. A rendering commit that retains a receipt
does not advance its approval baseline. Missing history falls back to complete
inputs. Upstream edits, removed sources and committed changes since that
baseline participate in the impact closure and root review delta.

A reading plan does not narrow the semantic scope of an independent review.
Mandatory project inputs absent from the plan retain their owning flow's read
obligations. The plan lets a role retrieve the right source portions in order;
it does not replace a review with a hash comparison. An unresolved relationship
is surfaced for investigation, not automatically rewritten by a reader.
Unrelated notes lacking typed relationships remain visible in the global gap
report; they do not force every task to read those notes. A selected note's gap
and unresolved or inconsistent references still reach the scoped reader.

## Verification and deployment

Real synthetic source files exercise reference ambiguity, exact revisions,
constraint preservation, oversized units, paginated continuation, snapshot
changes, external-source confinement and unchanged source bytes. Real Git
fixtures exercise approval events and source changes. No consumer corpus or
session transcript is distributed as a fixture.

Record cold index construction, unchanged queries, refresh after edits, returned
metadata/source bytes and extra reads separately. Functional replay must retain
every required source and seeded cross-unit defect before any speed improvement
is accepted. Model time is reported separately from resolver time.

The implementation uses Python's standard library and the existing JSON index.
Canonical code is shared by Claude Code and Codex; distributions are generated.
Path and encoding tests run in the normal Linux suite, and the existing native
Windows and macOS lanes cover their filesystem and lifecycle behavior. No new
service, database migration, release, or consumer configuration migration is
required. The reading-plan CLI is explicit opt-in; removing its invocation
restores the existing input flow.
