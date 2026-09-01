# Delivery Governance Flow

Spawn template: paste `{{constitution}}`, the current Fence state, Governance
receipt, Slot-state lens and `SELF-CHECK` into every reviewer prompt. Load the
`obsidian-vault` skill before writing the Delivery document.

Read this complete flow before `/configure governance` changes
`workspace/docs/delivery/governance/governance.md`. Governance is Delivery
coordination truth, not a project config field and not a Requirement stage.

1. `delivery-coordinator` is the sole writer. Create or revise the one global
   document with `delivery_governance.py`; `max_parallel` is a positive
   integer hard safety guard, not a product sizing or quality limit.
2. Run `delivery_governance.py check --json` and obtain the owner decision.
   A reduction is admissible only when all remote Slot references are free.
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
