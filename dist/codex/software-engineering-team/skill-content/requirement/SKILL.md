---
name: requirement
description: Orchestrate one Requirement from intake through approved planning inputs and backlog handoff.
exposure: entry
---

# Requirement

## When to Use

- A new user request, defect, technical change or initial-project change needs
  one traceable Requirement before backlog work.
- An existing Requirement must resume, revise, resolve with no change,
  withdraw or supersede under its state-valid action menu.

Before any docs operation, read the `obsidian-vault` skill. Its path,
frontmatter, designation and generated-view rules are authoritative.

## Procedure

1. Resolve the Git root and confirm the sole managed workspace is
   `workspace/`. Run the packaged `requirement_route.py` with the exact public
   argument grammar:

   ```text
   /requirement "<new intake>"
   /requirement REQ-###
   /requirement
   ```

   Free text always creates a new local Requirement proposal. An exact id
   resumes exactly that record. Bare invocation asks for new intake unless
   there is one eligible open record; it never fuzzy-resumes a record.

2. For a new proposal, run `requirement_compile.py init` and author only the
   Requirement record. Use `request_kind: feature|defect|technical`,
   `urgency: low|normal|high|critical`, a lower-kebab immutable slug and the
   four-row Stage Impact table. Direct user intake needs no fabricated
   `derives_from` note.

3. Run the compiler check. Present the exact normalized intent, outcome,
   scope/non-goals, evidence and Stage Impact matrix. Obtain the Requirement
   approval before expensive stage work. The compiler writes the UTC approval
   timestamp, semantic `source_hash` and status tag.

4. Dispatch only the rows marked `required` in order:
   `business-analysis`, `solution-design`, `design-system`, then
   `experience-design`. A `reuse` row must resolve to an approved valid target;
   `not_applicable` must retain its concrete rationale and exact empty evidence
   set. Every stage entry applies the same prerequisite check and may not skip
   an earlier required or reused stage.

5. Re-run the Requirement compiler after each stage handoff. A semantic impact
   change returns the Requirement to draft and requires a new approval. Stage
   approvals remain the existing stage compilers' gates; this entry does not
   duplicate their authoring logic.

6. Route to `/backlog-plan` only when the Requirement is approved, all required
   stage packages are approved/current, all reuse targets and N/A rows remain
   valid, and the shared Requirement incorporation predicate has no unresolved
   coverage error. `resolved_no_change` is the only terminal outcome that ends
   without a backlog delta.

7. Use only the state-valid exception actions. Discard removes one exact local
   uncommitted draft with no downstream output. Withdraw is allowed only for a
   committed or approved, nonterminal, unincorporated Requirement. Supersede
   requires a relation-bound replacement draft and writes the reciprocal
   relation atomically. Request changes and Stop leave bytes and refs unchanged.

8. Run the Requirement compiler, scoped vault gate and Git handoff check before
   reporting the next entry. Do not create Delivery branches, worktrees, slots,
   PRs or Release Management state from this entry.

## Compiler commands

```text
requirement_compile.py init --docs workspace/docs --slug <slug> \
  --title "<title>" --request-kind <feature|defect|technical> \
  --urgency <low|normal|high|critical>
requirement_compile.py check --docs workspace/docs --json
requirement_compile.py approve --requirement <path>
requirement_compile.py render --docs workspace/docs
requirement_route.py --project-root <root> [REQ-###|<new intake>] --json
```
