# Requirement Flow

Spawn template: paste `{{constitution}}` into every role prompt. Load the
`obsidian-vault` skill before reading or writing the docs tree; its vault law is
authoritative.

Vault first, in the order the constitution's section 5 sets: every role
queries the vault with the packaged `vault_query.py` verbs first, then
follows machine indexes and generated views, typed frontmatter, relation
blocks and wikilinks, then maps, and runs a targeted search only for a gap;
when the tools give too little or look wrong it uses its own methods and
records that it did. This flow starts from `home.md`,
`maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, the Requirement record's evidence links
and the approved stage packages' receipts.
Switch `context_pack`: at `role_digest`, a spawned role receives its pack from
`context_pack.py build --entry <entry> --role <role> --mode <mode>
--project-root <root>` instead of the full required reads; it reads a named
source in full only when the pack does not cover a case, and records that read
and why, as
`skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines.
Switch `step_timing`: at `recorded`, the coordinator runs `step_timing.py
start --run <run> --step <step> [--budget <id>]` before each step and
`step_timing.py end --run <run> --span <span>` after it, and records each role
spawn with `--kind spawn --parent <step span> --role <role> --phase
reading|writing|review|re_review|waiting`. An end that returns overrun is
reported to the owner at once with its breakdown, largest contributor and
lever. The run ends with `step_timing.py report --run <run> --write`, as
`skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines.
Switch `step_budgets`: at `enforced`, each budgeted step is compared with its
maximum, the owner's parameter or the package target in
`skill-content/configure/data/step-budgets.json`; a step may finish well under
it, and only an exceeded maximum is reported, never blocking, as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md`
defines.

This flow is the host-neutral sequence behind `/requirement` and the four
stage-by-stage entries. Durable truth is the Requirement record and the
approved stage packages under `workspace/docs/`; no runtime session or hidden
workflow mode is created.

## Sequence

1. Parse the public argument. Free text creates a new local Requirement,
   `REQ-###` selects exactly one record, and a bare invocation never fuzzy
   resumes an old record.
2. Compile and present Intent, Outcome/Acceptance, Scope/Non-goals,
   Evidence/Constraints and the four-row Stage Impact matrix.
3. Require Requirement approval before expensive stage work. The compiler
   owns the UTC approval stamp, semantic source hash and status tag.
   Switch `requirement_fact_check`: at `pre_approval_reader`, a read-only
   reader checks a technical Requirement's outcomes against the files they
   cite before its approval question, as
   `skill-content/requirement/references/switch-requirement_fact_check-pre_approval_reader.md`
   defines.
   Switch `review_scope`: at `impact_closure`, `requirement_compile.py check`
   runs before any reader is spawned and the fact-check reader reads the cited
   files' closure; `task_inputs.py` derives each reader's inputs with
   `--changed <note>` and `--base <approved commit>`, and a re-check given
   `--findings` derives only the fix delta; every role reads the change's
   impact closure, each approved, unchanged note outside it only as its
   hash-bound summary, reads beyond it when unsure and records why, a writer
   heals a missing relation with `impact_closure.py heal` and recomputes the
   closure, and a confirmation re-review reads only the fix's delta, as
   `skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
   defines.
4. Run only `required` stages in dependency order. `reuse` resolves to an
   approved current package; `not_applicable` has no evidence refs and keeps a
   concrete rationale. Every stage entry checks the same prerequisites. Each
   completed stage writes a compiler-owned Stage Results receipt with its exact
   result reference and hash; Stage Results are excluded from the semantic hash.
   Experience Design is one aggregate stage result: the globally current
   `application@rN` and its exact current process receipts are bound together.
   Reuse must verify the complete set, not process receipts alone.
5. Recheck the Requirement after every stage handoff. Semantic impact changes
   invalidate approval and require a new approval before downstream work. Any
   approved Experience package-set or application-only delta advances the
   global application receipt and makes an older Experience Stage Result
   non-current. Rebind it through the normal Requirement revision before
   backlog handoff.
6. Run Backlog Planning only when all required stage packages are current and
   the compiler-owned incorporation predicate is still open. An approved
   `resolved_no_change` Requirement is the only legal no-backlog terminal.

## User outcomes

Normal gates expose Approve, Request changes and Stop for now. Request changes
keeps the current draft and findings; Stop performs no mutation and leaves the
same exact resume command. Discard, Withdraw and Supersede are state-derived
exceptions and are never inferred from a generic rejection.

## Handoff

Backlog approval is committed through the project's ordinary Git policy. Only
after the exact Requirement and backlog revision reach target may Delivery
Planning consume them. Bring that target into a local checkout only with the
two Git synchronization forms the host contract names, `merge --no-edit
<source>` or `restore --source=<source> --worktree --
workspace/docs/experience-design`, each run with Git's absolute path as its own
call from the checkout root. Requirement Flow never creates Delivery branches,
worktrees, slots, PRs or Release Management state.
