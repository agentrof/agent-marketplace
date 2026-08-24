---
name: business-analysis
description: Interactive, compiler-backed business analysis that produces one approved project-local analysis space before solution, design and backlog work.
exposure: entry
---

# Business Analysis

Turn an idea into one approved analysis space. The space is tracked Markdown
under `workspace/docs/business-analysis/`; its compiler and the Obsidian vault
checker are the only mechanical authorities.

## When to Use

- A product idea, problem or change needs goals, actors, rules, acceptance
  criteria, assumptions and measurable non-functional budgets.
- The owner wants to update an existing analysis topic rather than create a
  second copy.

## Procedure

1. Read `flows/business-analysis.md` completely and resolve mode before any
   mutation. An exact `REQ-###` is Requirement mode and must match the
   router's `business-analysis` action. No Requirement argument is manual
   mode: never search for, create, or bind a Requirement in that mode.
2. Confirm the project-local `workspace/config.json` is owned by
   `software-engineering-team`. Read the `obsidian-vault` skill and the
   `requirements-analysis` skill completely. Run:

   ```text
   vault_check.py check --vault workspace/docs --scope business-analysis --json
   ```

   Run the packaged `vault_check.py`.

   Repair findings before authoring. The approved documents are the complete
   stage state.
3. For a new topic run:

   ```text
   ba_compile.py init --space workspace/docs/business-analysis/<slug> --title "<title>" --code <CODE>
   ba_compile.py render --space workspace/docs/business-analysis/<slug>
   ```

   Run the packaged `ba_compile.py`.

   An existing topic is UPDATE mode. Keep one living tree and preserve stable
   IDs; retire an obsolete row instead of renumbering or reusing it.
4. `business-analyst` is the sole writer. Work from
   goal → actor → behavior → rule → acceptance criterion. Put every fact in its
   owning typed document; link references instead of duplicating rows. Cover
   empty, boundary, wrong-role, stale and concurrent cases, and quantify each
   non-functional budget.
5. After each authoring milestone run `ba_compile.py check` and `render`, then
   run the scoped vault check. A red compiler or vault check stops the session.
6. Challenge each domain and the complete space with fresh, read-only
   reviewers. Reviewers return structured findings to this workflow; they do
   not write files. The Business Analyst resolves each blocking finding in its
   owning analysis document, or records the unresolved fact as an existing
   assumption, open question or decision row. Re-run only the affected
   challenge after fixes until no blocking evidence gap remains. Do not create
   review-history documents, round counters or lock state.
7. Close the gates in order:

   - foundation documents and their links are complete;
   - each domain has no unresolved blocking question or challenge finding;
   - the complete space passes `ba_compile.py check --gate approval`;
   - individual records are approved through the compiler's `approve` command;
   - `ba_compile.py approve-package --space <space> --vault-root workspace/docs`
     stamps the immutable package receipt.

   Never hand-write approval timestamps. The compiler stamps UTC evidence.
8. An approved space changes only after
   `ba_compile.py begin-revision --space <space> --doc <relative-doc>`.
   Commit the authored and generated analysis documents together. In
   Requirement mode bind the returned receipt with `requirement_compile.py
   bind-stage`; in manual mode report the exact receipt and suggest
   `solution-design`. Do not create backlog,
   delivery, task or release records in this entry.
