# Design System Flow

Spawn template: paste `{{constitution}}`, exact BA/Solution receipts, MASTER
and override paths, review lens and `SELF-CHECK` into every reviewer prompt.

Vault first, in the order the constitution's section 5 sets: every role
queries the vault with the packaged `vault_query.py` verbs first, then
follows machine indexes and generated views, typed frontmatter, relation
blocks and wikilinks, then maps, and runs a targeted search only for a gap;
when the tools give too little or look wrong it uses its own methods and
records that it did. This flow starts from `home.md`,
`maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, MASTER and its page overrides with their
`uses_design` back-links.
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

Read this complete flow before `/design-system` changes durable state. Exact
`REQ-###` selects router-bound Requirement inputs. Manual mode requires an
explicit strict-current BA and Solution package selection, never a guessed
Requirement. Legacy-readonly receipts are only valid for explicit Requirement
reuse, not for a new or revised MASTER.

Load `obsidian-vault` before writing under `workspace/docs/`; its policy owns
the catalog artifact path and relative-artifact link law.

1. `ux-designer` is the only writer. MASTER carries contract version 3, exact
   BA `derives_from` and Solution `constrained_by` bindings, and the marked
   catalog token block.
2. Creation writes MASTER and `artifacts/standalone.html` together. The
   standalone catalog has a fixed section/slot order but gets every visual
   value and visible project text from MASTER; it never supplies a fictional
   brand asset or project example.
3. Before review run `design_system_compile.py sync-catalog --root
   workspace/docs/design-system`. Revisions are ordered: begin-revision,
   MASTER update, catalog update, sync-catalog, review, check, approve.
4. Spawn `design-system-reviewer` read-only with MASTER, catalog, page
   overrides and the semantic token, accessibility and contradiction lens.
   Switch `review_panels`: at `lens_panel`, review panel `design_system`
   replaces this reviewer, as
   `skill-content/challenge-review/references/switch-review_panels-lens_panel.md`
   defines. Switch `review_loop`: at `blocking_delta`, this review loop follows
   `skill-content/challenge-review/references/switch-review_loop-blocking_delta.md`.
   Switch `review_rounds`: at `single_pass`, this review runs as one reader and
   one pass, following
   `skill-content/challenge-review/references/switch-review_rounds-single_pass.md`.
   Switch `reader_waves`: at `all_at_once`, every reader of a review or recheck
   wave starts at once, after every finished worker is closed, as
   `skill-content/challenge-review/references/switch-reader_waves-all_at_once.md`
   and the host contract define.
   Switch `review_scope`: at `impact_closure`, `design_system_compile.py
   check` runs before any reader is spawned; `task_inputs.py` derives each
   reader's inputs with `--changed <note>` and `--base <approved commit>`, and
   a re-check given `--findings` derives only the fix delta; every role reads
   the change's impact closure, each approved, unchanged note outside it only
   as its hash-bound summary, reads beyond it when unsure and records why, a
   writer heals a missing relation with `impact_closure.py heal` and
   recomputes the closure, and a confirmation re-review reads only the fix's
   delta, as
   `skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
   defines.
   Switch `review_fanout`: at `per_unit`, a review of more than one changed
   page override, with MASTER as one unit, spawns one reader per unit in one
   message and then one aggregator for the cross-unit checks no compiler
   enforces, as
   `skill-content/challenge-review/references/switch-review_fanout-per_unit.md`
   defines.
5. Run compiler checks, then `approve`; changes to an approved MASTER begin a
   revision first. Compiler approval and a committed package are handoff.
4. Requirement mode binds its receipt. Manual mode returns it and suggests
   `/experience-design` without automatic dispatch.
