# Delivery Governance Flow

Spawn template: paste `{{constitution}}`, the current Fence state, Governance
receipt, Slot-state lens and `SELF-CHECK` into every reviewer prompt. Load the
`obsidian-vault` skill before writing the Delivery document.

Vault first, in the order the constitution's section 5 sets: every role
queries the vault with the packaged `vault_query.py` verbs first, then
follows machine indexes and generated views, typed frontmatter, relation
blocks and wikilinks, then maps, and runs a targeted search only for a gap;
when the tools give too little or look wrong it uses its own methods and
records that it did. This flow starts from `home.md`,
`maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, the Governance document, the Fence state
and the Slots it names.
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

Read this complete flow before `/configure governance` changes
`workspace/docs/delivery/governance/governance.md`. Governance is Delivery
coordination truth, not a project config field and not a Requirement stage.

1. `delivery-coordinator` is the sole writer. Create or revise the one global
   document with `delivery_governance.py`; `max_parallel` is a positive
   integer hard safety guard, not a product sizing or quality limit.
2. Run `delivery_governance.py check --json` and obtain the owner decision.
   A reduction is admissible only when all remote Slot references are free.
   Switch `owner_gates`: at `two_fixed_gates`, a change that a Delivery's
   execution plan needs is decided in that Delivery's gate A and applied before
   any of its Items starts, as
   `skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md`
   defines.
   Switch `review_scope`: at `impact_closure`, `delivery_governance.py check
   --json` runs before any reader is spawned; `task_inputs.py` derives each
   reader's inputs with `--changed <note>` and `--base <approved commit>`, and
   a re-check given `--findings` derives only the fix delta; every role reads
   the change's impact closure, each approved, unchanged note outside it only
   as its hash-bound summary, reads beyond it when unsure and records why, a
   writer heals a missing relation with `impact_closure.py heal` and
   recomputes the closure, and a confirmation re-review reads only the fix's
   delta, as
   `skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
   defines.
3. Approve with `delivery_governance.py approve`, then run
   `delivery_git.py apply-governance --project-root <project>`. The latter
   reads the approved document and computes the hash itself; callers never
   supply a hash or parallelism value.
4. Do not start, resume, reopen or take over an Item while the Governance
   Fence handoff is held. Return the exact governance receipt and Fence
   handoff state.
5. A protocol-1 Fence is readable only to perform the one-way
   `delivery_git.py upgrade-fence-v1` migration. It must be open and have no
   allocated Slots; the command pins the approved Governance hash before any
   new Item mutation is permitted.
