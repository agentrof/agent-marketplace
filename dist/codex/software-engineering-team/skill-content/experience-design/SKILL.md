---
name: experience-design
description: Maintain living, process-owned Experience packages and their one canonical application after approved analysis, Solution Design and Design System inputs. Supports Requirement and manual entry before backlog planning.
exposure: entry
---

# Experience Design

Model and revise living user experiences without implementing product code.

## When to Use

Use after approved BA, Solution and Design System inputs need user-journey,
screen, flow, state or application work, either from an exact Requirement or a
manual/direct product-design chain.

1. Read `workspace/config.json`, `flows/experience-design.md`,
   `experience-modeling` and `obsidian-vault` in full.
2. Determine the mode from an explicit `REQ-###`; do not infer a Requirement
   in manual mode. Validate all upstream receipts before writing.
3. Run the read-only scope proposal and retain its JSON only as a transient
   local file. Obtain the user's exact action-set approval, then pass that
   file and its hash to every lifecycle mutation and atomic approval. The plan
   must pin the current application receipt and say whether the application
   changes, including an independent application-only revision.
4. The primary process ref is
   `business-analysis/<space>/processes/<process-slug>-process`; it must
   resolve through the selected approved BA package. Work only in the selected
   `workspace/docs/experience-design/experiences/<process-slug>/` packages,
   their `artifacts/application-map.json` files and the single
   `workspace/docs/experience-design/artifacts/application.html`, using the
   Experience compiler. `application` is a reserved process slug and alias.
   There are no EXP IDs, `exp-` directories, baselines, programs, releases or
   inheritance chains.
5. Use stable child IDs and exact refs. The package owns approval; children
   use `record_state`. Map every active exact ref to one or more declared deep
   routes in the fixed declarative, network-free application. Bind the
   application to the exact approved contract-v3 Design System and require
   every active process package to bind that same receipt. Generated and ledger
   data are compiler-owned; package-local previews and manifests are invalid.
6. Run the transient, fresh reviewer challenge loop defined by the flow.
   Close blockers in canonical records, not in review-history artifacts.
7. Atomically approve the complete create/update/rename/retire and application
   action set. The result is a new globally current `application@rN` plus the
   exact current process receipts. Requirement mode binds that complete set;
   manual mode returns it to `backlog-plan` directly. A downstream consumer of
   an older application receipt must rebind through its normal revision.
