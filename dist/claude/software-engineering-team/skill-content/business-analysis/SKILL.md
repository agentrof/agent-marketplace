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

1. Confirm the project-local `workspace/config.json` is owned by
   `software-engineering-team`. Read the `obsidian-vault` skill and the
   `requirements-analysis` skill completely. Run:

   ```text
   vault_check.py check --vault workspace/docs --scope business-analysis --json
   ```

   Run the packaged `vault_check.py`.

   Repair findings before authoring. The approved documents are the complete
   stage state.
2. For a new topic run:

   ```text
   ba_compile.py init --space workspace/docs/business-analysis/<slug> --title "<title>" --code <CODE>
   ba_compile.py render --space workspace/docs/business-analysis/<slug>
   ```

   Run the packaged `ba_compile.py`.

   An existing topic is UPDATE mode. Keep one living tree and preserve stable
   IDs; retire an obsolete row instead of renumbering or reusing it.
3. Adopt the Business Analyst role in the current conversation. Work from
   goal → actor → behavior → rule → acceptance criterion. Put every fact in its
   owning typed document; link references instead of duplicating rows. Cover
   empty, boundary, wrong-role, stale and concurrent cases, and quantify each
   non-functional budget.
4. After each authoring milestone run `ba_compile.py check` and `render`, then
   run the scoped vault check. A red compiler or vault check stops the session.
5. Challenge each domain and the complete space. Record round 1 under the
   space `reviews/` folder, disposition each finding as fix, covered,
   assumption, question or rejected-with-reason, and run at most three rounds
   while blocking findings remain. Review notes are ordinary tracked
   Markdown.
6. Close the gates in order:

   - foundation documents and their links are complete;
   - each domain has no unresolved blocking question;
   - the complete space passes `ba_compile.py check --gate approval`;
   - the project decision authority approves through the compiler's `approve` command.

   Never hand-write approval timestamps. The compiler stamps UTC evidence.
7. Commit the authored and generated analysis documents together. Report the
   approved space and route to `solution-design`. Do not create backlog,
   delivery, task or release records in this entry.
