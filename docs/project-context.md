# Project context resolution

`project_context.py` prepares a bounded reading plan from references that an
entry has already selected. It returns source paths, record or section
addresses, reasons and byte sizes. It never chooses skills, changes a source,
executes an environment command or creates approval authority.

## Workflow

1. The entry selects scope through its existing compiler and derives the normal
   `task_inputs.py` manifest. Every project manifest automatically includes
   `project_reading`, resolved from those selected sources using entry, role and
   mode. No extra flag is needed. A task with no selected document sources gets
   `needs_scope`, not an invented whole-vault read set.
2. Read `project_reading.must_read` using `project_context.py --project-root
   <root> read --plan <saved task manifest>`. The command accepts either the
   complete manifest or a standalone resolver plan. It returns the selected
   source text in one call, including table headers and governing parent items.
3. A `needs_split` plan has required work left. `expand --plan <path> --reason
   <reason>` takes the next bounded page, including UTF-8 fragments of an
   oversized logical unit. The parent obligation remains until its final byte
   is read. `units --ref <source>` lists addresses without replacing required
   scope. A budget smaller than the next character reports its exact minimum.
   `needs_resolution` requires investigating the reported relationship gaps.
4. A role may use manual search, file reads and relationship discovery whenever
   the plan is insufficient, incorrect or unavailable, on its own initiative
   or the parent agent's direction. Record extra sources and reasons and rebind
   evidence before relying on it. Preserve all owning-flow read, verification
   and approval obligations. An unavailable resolver is an explicit recovery
   state, never proof of complete reading.
5. Recheck with `check --plan <path>` before handoff. Standalone plans bind the
   vault snapshot. Automatic task plans bind the resolved selection, including
   required units beyond the first page; unrelated edits do not stale disjoint
   tasks. Re-resolution still detects changed edges, ambiguity and deleted
   sources. Hashes never prove that a reader has read the content.
6. An explicit `--context-plan <project-relative JSON>` can bind a custom plan
   instead of the automatic one. This does not change skill selection or scope.

Frozen Delivery verification manifests also include `project_reading`. Their
`delivery_verification.py inspect-context --plan <manifest>` command validates
the candidate and plan, then batch-reads exact Git blobs. `expand-context`
continues the plan under the same candidate binding and accepts reasoned
additional references. Ordinary resolver
`read` refuses frozen manifests. `inspect` and `diff` remain available for
manual recovery, and `full_read` and verification gates remain mandatory.

## Context feedback

Every role returns observed context failures or recurring retrieval friction in
`context_findings`: expected and actual behavior, source anchors, impact,
recovery attempted and its result, and a proposed fix and verification case
when known. Normal continuation and a correctly reported absent document are
not automatically defects. The parent groups duplicates and routes project-only
authoring gaps to their owner. For plugin defects or improvements it follows
`issue-report`: anonymize the case, preview the exact issue with its proposed
solution, obtain explicit user approval, then file only that payload.

`file_issue.py --preview` performs the privacy checks and prints a payload hash
without network access. Filing requires `--approved-payload-sha256` matching
the displayed title/body and fixed target. The hash binds content; the human
choice gate supplies consent. Changed text requires a new preview and approval.
No transcript upload, automatic telemetry, local issue queue or background
worker is introduced. Declining reporting never blocks normal project work.

## Policy and source types

`templates/project-context-policy.json` declares entry profiles, role
preferences, required versus optional relation keys, supported purposes,
source roots, purpose-specific required relationships and separate limits for initial file count, source bytes and
metadata bytes. It contains no project-specific paths or topic vocabulary.
The existing task policy validates which roles belong to each entry.

`context_catalog.py` uses the compiler's Markdown parser. It addresses qualified
BA records, scenarios, current component/architecture identities, stable block
IDs and exact architecture or Experience ledger receipts. Ambiguous bare IDs
are refused; callers qualify them or use an exact path. Section addresses are
internal selectors tied to a source hash, not new heading wikilinks in the vault.
Immutable ledger receipts take precedence over current Markdown aliases and
retain their exact revision even if the current document still declares it. Hash-pinned Operation, Requirement and Backlog sources are
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

The implementation uses Python's SQLite/FTS5 support and a checkout-local
`.agentrof/agent-marketplace/.runtime/vault-index/index.db`. The first writable
query builds from canonical sources. Later queries reconcile eligible source
hashes, reparse changed sources, re-resolve the references whose identities,
owner claims or excluded link targets changed, and update catalog, graph and
FTS together. Text mentions are rescanned only for changed sources unless the
identifier set they match changes. Queries read indexed records rather than
loading a full JSON catalog. `--no-cache` and read-only filesystems reconcile a
private temporary copy of a compatible published cache, or compile in memory,
without project writes. Small verified request/provenance capsules retain their
existing JSON and bounded inline formats; they are not a second vault index.

The scope is `.md` and `.json` beneath `workspace/docs`, excluding `artifacts`
directories at every depth and the declared Obsidian/trash exclusions.
Markdown notes keep the vault's exact `.md` rule. Another spelling of an
eligible extension, or JSON that does not parse, stays inventoried as an
unparsed source and is reported instead of blocking navigation; a canonical
receipt that does not parse names its path in the refusal. JSON sources are
addressable for reading and section search, never graph identities or notes.
A linked file inside the vault is read like its target, a link leaving the
vault is refused, and directory links are not followed. Source class and
authority still follow vault and owning-compiler contracts. Artifact integrity
and approval checks retain excluded inputs. Every main checkout or Git worktree
owns its own database, bound to its resolved path. A missing initialized cache
builds automatically; an empty eligible scope has an empty schema. Indexing
does not create authored workspace content.

`vault_query.py --docs workspace/docs index ensure|sync|rebuild` provides cache
maintenance. `index status` reports the stored binding, schema, generation,
counts, unparsed sources and a stat-only `possibly_stale` signal without
rehashing; `index check` verifies source and integrity coverage. Neither takes
the writer lock beyond copying a consistent snapshot. `check` independently
recompiles the source projection in memory and compares catalog, namespaces,
relationships, gaps and search tokens; incomplete projections never report
`verified: true`, and a source edited during the check reports `stale`.
`rebuild` publishes fresh derived state in one transaction, preserving the last
generation when compilation fails. Only owned legacy JSON/shard projections are
cleaned after a successful full SQLite publication. `search-sections` returns
FTS candidate addresses; legacy `search` retains its line matching contract over
notes and the machine records their edges cite.

A cache bound to another checkout path, another derivation or schema, or a
full-text layout this SQLite cannot open is never served: the next query
recompiles it from this checkout's sources. Physically corrupt disposable
databases are recompiled from canonical sources. A complete replacement is
built before publication and waits for existing readers to close; failed
compilation preserves the prior files and separate reading-state capsules.
SQLite readonly errors use the same source-derived fallback as filesystem
permission failures. Reader cleanup does not wait on long compiler work, and
read-only inspections verify their copied bytes against a concurrent WAL
checkpoint before trusting the snapshot. When `vault_query.py` or
`project_context.py` first discovers corruption while executing a query, it
closes that reader and rebuilds a complete database before one bounded retry.
Repeated query failures remain explicit errors. A Python without SQLite or FTS5
receives that diagnostic from setup and an unavailable resolver plan.

Both hosts use the shared post-write hook after source validation. Its index
sync is a hint: it never waits for another index writer and never fails or
delays the hook's own outcome; external edits are reconciled before queries. No
watcher dependency or background service is installed. SQLite serializes index
writers while existing workflow scopes continue to coordinate authored-file
mutations. Canonical code and generated distributions remain shared by Claude
Code and Codex. Linux runs every index case; native Windows runs its lock,
file-replacement, path-spelling and process cases, and both native systems run
the case-sensitive link case. No database server or consumer
source/configuration migration is required. Existing review and approval gates
remain authoritative.
