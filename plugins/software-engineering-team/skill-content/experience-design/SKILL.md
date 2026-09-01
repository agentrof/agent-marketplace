---
name: experience-design
description: Maintain living, process-owned Experience packages and author-owned prototype snapshots after approved analysis, Solution Design and Design System inputs.
exposure: entry
---

# Experience Design

Model and revise living user experiences without implementing delivery code.

## When to Use

Use after approved BA, Solution Design and Design System inputs need
user-journey, screen, flow, state, transition or prototype work.

1. Read `workspace/config.json`, `flows/experience-design.md`,
   `experience-modeling` and `obsidian-vault` in full.
2. Determine Requirement or manual mode exactly, validate upstream receipts,
   run the read-only scope proposal and obtain approval for its complete action
   set before any lifecycle mutation.
3. If a scope composed only of `draft` or `in_review` non-retire mutations is
   stranded on an obsolete proposal, generate a fresh recovery proposal from
   the exact old plan and current bindings, then run `recover-open-scope` with
   both exact plans and hashes. The fresh plan binds the old proposal hash.
   Recover the complete old package set atomically, reset it to `draft` for a
   new review, preserve authored child records and prototype/package artifact
   bytes, and leave approved ledgers and receipts untouched. Fail closed if
   the scope also contains `retirement_pending` or a retire action. A legacy
   package may carry only the fresh plan's exact current input bindings; every
   other package identity, revision and Requirement binding stays exact.
4. If recovery reports that those exact open package revisions were already
   published by `application@rN`, run `rehydrate-published-scope` with the old
   plan, its hash and that exact application ref. It proves the complete scope
   still reproduces every published package hash, restores only the published
   approved package roots, leaves application receipts and artifacts unchanged,
   then requires a normal current `begin-revision`. Hash drift, a partial
   scope, a conflicting receipt or stale open application state fails closed.
5. Work in the selected
   `workspace/docs/experience-design/experiences/<process-slug>/` packages.
   The primary process is a canonical BA process. `application` is reserved;
   there are no EXP IDs, baselines, programs, releases or inheritance chains.
6. Use stable child IDs and exact refs. Package records express process and
   product intent; the separate prototype demonstrates it for review.
7. Treat `workspace/docs/experience-design/artifacts/` as the UX designer's
   free prototype workspace. It may contain any structure, files, pages,
   technologies, dependencies and assets. Recommend useful conventions, but
   never require them or make their absence a compiler finding. Do not put
   lifecycle metadata in those files.
8. Keep `_generated/` and `_ledger/` compiler-owned. The compiler snapshots
   artifact paths and bytes, then binds the snapshot to its process receipt
   set. It does not validate UI structure, CSS, scripts, network behavior,
   tokens, framework choices, routes or accessibility claims.
9. Run the fresh read-only reviewer challenge loop. Review actual usability,
   coherence, accessibility, responsive behavior and risks as judgment, not
   as a substitute parser contract.
10. Atomically approve the complete action set. The result is `application@rN`
   plus the exact current process receipts. Requirement mode binds that set;
   manual mode hands it to backlog planning.
