# Delivery Planning Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Vault first, per constitution section 5: every role starts from the default `project_reading` plan.
Batch-read its units, using the frozen context reader for frozen candidates. When context is insufficient, wrong or unavailable, use manual search, reads and relationship discovery
on the role's initiative or parent direction; record sources and reasons, rebind evidence, preserve gates and return `context_findings` to the parent for user-approved reporting.
For gaps, use `vault_query.py`, machine indexes and generated views, typed frontmatter, relation blocks and wikilinks, then maps and targeted search.
Fallback navigation with a shell can use `home.md`, `maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, `maps/backlog.md`,
`maps/delivery.md` and `backlog_compile.py check --json` for the story
graph. Switch `context_pack`: at `role_digest`, a spawned role receives
its pack from `context_pack.py build --entry <entry> --role <role>
--mode <mode> --project-root <root>` instead of the full required reads;
it reads a named source in full only when the pack does not cover a
case, and records that read and why, as
`skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines. Switch `step_timing`: at `recorded`, the coordinator runs
`step_timing.py start --run <run> --step <step> [--budget <id>]` before
each step and `step_timing.py end --run <run> --span <span>` after it,
and records each role spawn with `--kind spawn --parent <step span>
--role <role> --phase reading|writing|review|re_review|waiting`. An end
that returns overrun is reported to the owner at once with its
breakdown, largest contributor and lever. The run ends with
`step_timing.py report --run <run> --write`, as
`skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines. Switch `step_budgets`: at `enforced`, each budgeted step is
compared with its maximum, the owner's parameter or the package target
in `skill-content/configure/data/step-budgets.json`; a step may finish
well under it, and only an exceeded maximum is reported, never blocking,
as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md`
defines.

`/delivery-plan` is the user-facing scope flow. It selects one exact backlog
story set, checks current Requirement and Definition of Done evidence, renders a
temporary proposal, obtains the Delivery Scope decision and then hands the
approved files to the explicit Git coordinator. No timebox, slot, branch,
worktree or release field belongs in this flow.
Switch `owner_gates`: at `two_fixed_gates`, the scope decision moves into gate A
at the end of execution planning, and questions before it are queued, as
`skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md`
defines.
Switch `review_scope`: at `impact_closure`, `init` runs its binding checks
before any reading role is spawned and the selection is read as the selected
stories' closure, with every other story as its summary; `task_inputs.py`
derives each reader's inputs with `--changed <note>` and `--base <approved
commit>`, and a re-check given `--findings` derives only the fix delta; every
role reads the change's impact closure, each approved, unchanged note outside
it only as its hash-bound summary, reads beyond it when unsure and records
why, a writer fixes a reported graph gap through its owning compiler and
recomputes the closure, and a confirmation re-review reads only the fix's
delta, as
`skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
defines.

The proposal is disposable until reservation. A declined or interrupted
proposal leaves the target checkout, refs and authored vault unchanged. After
reservation, the Delivery ID, goal-derived slug and scope hash are immutable.

`init` checks the selection's upstream bindings before it renders the
proposal, and scope approval checks them again before the handoff, since a
binding can change in between. Each refuses a selected Story whose implemented
Requirement does not route to backlog; rebind that Requirement through
`/requirement REQ-###` first. A superseded, withdrawn or `resolved_no_change`
Requirement cannot be rebound, so a backlog revision re-traces the Story to a
current Requirement, such as the named successor, or drops it. When a selected
Story cites `experience_refs`, the backlog must bind the globally current
`application@rN` in its compiler-owned `input_bindings`; otherwise the finding
names that receipt and the check refuses until a backlog revision binds it. A
manual-mode revision pins it with `--input-ref`. A requirement-mode revision
takes it from the root Requirement's Experience Stage Results, rebound first
through `/requirement REQ-###`, or, when that Requirement marks Experience
`not_applicable`, from `--input-ref` at `begin-revision`. Until its next
revision, a requirement-mode backlog approved before it carried
`input_bindings` binds through its root Requirement's Experience Stage Results
instead. A reserved Delivery keeps verifying its pinned inputs historically.

Switch `story_size_budget`: at `propose_split`, `init` also prints each
selected Story's size measures and over-budget flags as `story_size`, which
the proposal shows read-only, as
`skill-content/product-planning/references/switch-story_size_budget-propose_split.md`
defines.

Switch `test_cost_budget`: at `flag_serial_rows`, `init` also prints the
selected Stories' scenarios over the serial-row limit as `test_cost`, each
naming its `level` while `test_levels` is `declared`, which the proposal shows
read-only, as
`skill-content/product-planning/references/switch-test_cost_budget-flag_serial_rows.md`
defines.

Switch `delivery_path`: at `light_when_eligible`, `init` also reports whether
the selection may take the light path, and a Delivery the compiler finds
eligible plans its scope and its Item topology in this flow with one owner
gate, then runs scope approval, reservation, execution approval, publication
and claims in order, as
`skill-content/delivery-plan/references/switch-delivery_path-light_when_eligible.md`
defines; every other Delivery follows this flow unchanged.
