# Design System Flow

Spawn template: paste `{{constitution}}`, exact BA/Solution receipts, MASTER
and override paths, review lens and `SELF-CHECK` into every reviewer prompt.

Read this complete flow before `/design-system` changes durable state. Exact
`REQ-###` selects router-bound Requirement inputs. Manual mode requires an
explicit strict-current BA and Solution package selection, never a guessed
Requirement. Legacy-readonly receipts are only valid for explicit Requirement
reuse, not for a new or revised MASTER.

1. `ux-designer` is the only writer. MASTER carries exact BA `derives_from`
   and Solution `constrained_by` bindings.
2. Spawn `design-system-reviewer` read-only with MASTER, page overrides and
   the semantic token, accessibility and contradiction lens.
3. Run compiler checks, then `approve`; changes to approved MASTER begin a
   revision first. Compiler approval and a committed package are handoff.
4. Requirement mode binds its receipt. Manual mode returns it and suggests
   `/experience-design` without automatic dispatch.
