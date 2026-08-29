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
3. Work in the selected
   `workspace/docs/experience-design/experiences/<process-slug>/` packages.
   The primary process is a canonical BA process. `application` is reserved;
   there are no EXP IDs, baselines, programs, releases or inheritance chains.
4. Use stable child IDs and exact refs. Package records express process and
   product intent; the separate prototype demonstrates it for review.
5. Treat `workspace/docs/experience-design/artifacts/` as the UX designer's
   free prototype workspace. It may contain any structure, files, pages,
   technologies, dependencies and assets. Recommend useful conventions, but
   never require them or make their absence a compiler finding. Do not put
   lifecycle metadata in those files.
6. Keep `_generated/` and `_ledger/` compiler-owned. The compiler snapshots
   artifact paths and bytes, then binds the snapshot to its process receipt
   set. It does not validate UI structure, CSS, scripts, network behavior,
   tokens, framework choices, routes or accessibility claims.
7. Run the fresh read-only reviewer challenge loop. Review actual usability,
   coherence, accessibility, responsive behavior and risks as judgment, not
   as a substitute parser contract.
8. Atomically approve the complete action set. The result is `application@rN`
   plus the exact current process receipts. Requirement mode binds that set;
   manual mode hands it to backlog planning.
