---
name: experience-design
description: Maintain approved living, process-owned Experience packages after approved analysis, Solution Design and Design System inputs. Supports Requirement and manual entry before backlog planning.
exposure: entry
---

# Experience Design

Model and revise living user experiences without implementing product code.

## When to Use

Use after approved BA, Solution and Design System inputs need user-journey,
screen, flow, state or artifact work, either from an exact Requirement or a
manual/direct product-design chain.

1. Read `workspace/config.json`, `flows/experience-design.md`,
   `experience-modeling` and `obsidian-vault` in full.
2. Determine the mode from an explicit `REQ-###`; do not infer a Requirement
   in manual mode. Validate all upstream receipts before writing.
3. Run the read-only scope proposal and retain its JSON only as a transient
   local file. Obtain the user's exact action-set approval, then pass that
   file and its hash to every lifecycle mutation and atomic approval.
4. The primary process ref is
   `business-analysis/<space>/processes/<process-slug>-process`; it must
   resolve through the selected approved BA package. Work exclusively in
   `workspace/docs/experience-design/experiences/<process-slug>/` using the
   Experience compiler. There are no EXP IDs, `exp-` directories, baselines,
   programs, releases or inheritance chains.
5. Use stable child IDs and exact refs. The package owns approval; children
   use `record_state`. Generated and ledger data are compiler-owned.
6. Run the transient, fresh reviewer challenge loop defined by the flow.
   Close blockers in canonical records, not in review-history artifacts.
7. Atomically approve the complete changed package set. Requirement mode then
   binds the receipt set; manual mode returns it to `backlog-plan` directly.
