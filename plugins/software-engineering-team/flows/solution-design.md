# Solution Design Flow

Spawn template: paste `{{constitution}}`, the exact BA receipt, tree paths,
decision-status lens and `SELF-CHECK` into every reviewer prompt.

Vault first, in the order the constitution's section 5 sets: every role
queries the vault with the packaged `impact_closure.py` verbs first, then
follows machine indexes and generated views, typed frontmatter, relation
blocks and wikilinks, then maps, and runs a targeted search only for a gap;
when the tools give too little or look wrong it uses its own methods and
records that it did. This flow starts from `home.md`,
`maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, the landscape's `decision-log.md` and
its `_generated/capability-registry.json`, `component-catalog.json` and
`topology.json`.
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

Read this complete flow before `/solution-design` changes durable state.
Exact `REQ-###` selects Requirement mode; its strict-current BA receipt is the only
input. No Requirement argument is manual mode and requires an explicit exact
strict-current BA package selection. A legacy-readonly BA package may only be
bound as an explicit Requirement reuse, never used to author a new Solution revision.

1. `solution-architect` is the only writer. Before accepting topology, it
   allocates each active BA process to one explicit component or records a
   rationale-bearing `not_technical` disposition in the landscape. It compares
   meaningful monolith, modular-monolith, distributed or hybrid alternatives.
2. The user explicitly confirms the selected topology and the complete naming
   set: every project-built deployable app, its lower-kebab ID, responsibility,
   app kind and canonical future `workspace/apps/<app-id>` path. Build apps
   are components; self-hosted, managed and third-party dependencies are
   components but never project app directories.
3. Author `components/<component-id>/component.md` plus accepted technology,
   data-store, environment and integration decisions. A component may use a
   different accepted stack than another component. Proposed, in-review,
   rejected and superseded decisions never constrain an approved topology.
4. Follow the single review plan in
   `skill-content/solution-architecture/references/challenge-lenses.md`.
   Spawn one independent primary `solution-reviewer` for all four required
   lenses plus BA allocation, topology, naming, sourcing and decision status.
   Add only the plan's risk-triggered specialist invocations; they do not form
   a second default panel. Wait for every selected reader before the writer
   resolves blockers in canonical landscape/components/decisions. Replies are
   transient; repeat only affected review when blocking evidence changes.
   Switch `review_panels`: at `lens_panel`, review panel `solution_design`
   replaces the primary reviewer, as
   `skill-content/challenge-review/references/switch-review_panels-lens_panel.md`
   defines. Switch `mechanical_pass_tier`: at `mechanical`, a writer pass that
   only applies the fixes returned findings name, and step 5's compiler
   commands, run as
   `skill-content/challenge-review/references/switch-mechanical_pass_tier-mechanical.md`
   defines.
   Switch `review_loop`: at `blocking_delta`, this review loop follows
   `skill-content/challenge-review/references/switch-review_loop-blocking_delta.md`.
   Switch `review_rounds`: at `single_pass`, this review runs as one reader and
   one pass, following
   `skill-content/challenge-review/references/switch-review_rounds-single_pass.md`.
   Switch `reader_waves`: at `all_at_once`, every reader of a review or recheck
   wave starts at once, after every finished worker is closed, as
   `skill-content/challenge-review/references/switch-reader_waves-all_at_once.md`
   and the host contract define.
   Switch `review_scope`: at `impact_closure`, `landscape_check.py check` runs
   before any reader is spawned; `task_inputs.py` derives each reader's inputs
   with `--changed <note>` and `--base <approved commit>`, and a re-check
   given `--findings` derives only the fix delta; every role reads the
   change's impact closure, each approved, unchanged note outside it only as
   its hash-bound summary, reads beyond it when unsure and records why, a
   writer heals a missing relation with `impact_closure.py heal` and
   recomputes the closure, and a confirmation re-review reads only the fix's
   delta, as
   `skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
   defines.
   Switch `review_fanout`: at `per_unit`, a review of more than one changed
   component or decision record spawns one reader per unit in one message and
   then one aggregator for the cross-unit checks no compiler enforces, as
   `skill-content/challenge-review/references/switch-review_fanout-per_unit.md`
   defines.
5. After the owner confirms the exact topology and naming set, run
   `landscape_check.py confirm-topology`, then `check`, render
   capability/component/topology catalogs and package `approve`. Approval
   requires `topology_selected: true`, a version-3 confirmation receipt and a
   complete BA allocation universe. An approved package changes only through
   `begin-revision`.
6. Requirement mode binds the result. Manual mode returns the exact solution
   package receipt and suggests `/design-system`. It never creates an app,
   System Architecture record or Delivery Item.
