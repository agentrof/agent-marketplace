# Design System Flow

Spawn template: paste `{{constitution}}`, exact BA/Solution receipts, MASTER
and override paths, review lens and `SELF-CHECK` into every reviewer prompt.

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
5. Run compiler checks, then `approve`; changes to an approved MASTER begin a
   revision first. Compiler approval and a committed package are handoff.
4. Requirement mode binds its receipt. Manual mode returns it and suggests
   `/experience-design` without automatic dispatch.
