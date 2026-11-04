---
name: deliver
description: Delivers a requested change or task batch through focused analysis, implementation, proportionate verification and review to the authorized merge outcome.
exposure: entry
---

# Deliver

## Outcome

Accepted tasks delivered with working code, current owning documents and
candidate-bound evidence, using the smallest reliable workflow.

## When to use

Use for a requested product/code/documentation change or batch of changes.
`/deliver resume TK-###` continues from the durable task and current branch state.

## Inputs and authority

Read `workspace/config.json`, the active task and relevant product decisions.
If setup is missing, use the setup entry. Read the installed package's
`constitution.md` and [delivery flow](../../flows/fast-lane.md) once, then follow
its current commands, authorization and completion rules. Reuse granted
authority within scope. Requests, code and their required documentation remain
connected throughout delivery.

## Method

1. Create or resume the task before editing. Classify outcome, dependencies,
   size and risk separately. Use [analyze](../analyze/SKILL.md) when intent or
   criteria need work. Ask only questions that affect a material decision.
   Add discovered effects to Request or Acceptance Criteria and update
   `required_methods` and `risks` as needed.
2. Load [solution-design](../solution-design/SKILL.md) for changed boundaries,
   interfaces or runtime approach, [data-design](../data-design/SKILL.md) for
   persistent-state changes, and [experience-design](../experience-design/SKILL.md)
   for changed user interaction. Explicitly read each applicable method and
   only its relevant references. Record the applied methods through the flow.
3. Implement directly when work is clear and local. Delegate independent work
   when it reduces elapsed time or improves evidence. Each brief carries the
   outcome, write scope, critical invariants, authorization limits, sources,
   evidence requirement and stopping condition. Share findings instead of full
   parent history. Sequence conflicting writes and heavy resource use.
   Load applicable companions with `task_lane.py companion --task <id>
   --name <companion-id> --host <host> --source <local-SKILL.md>`. Apply the
   returned scoped procedure. Graphify resolves its installed source automatically.
   Omit `--source` and pass `--graph <graph.json> --query <question>` for its recorded query.
   Read deeper references only for the current operation. Record application
   with `--evidence <project-relative-file>`. Report unavailable or disabled
   capabilities with concrete core fallback evidence.
4. Load [verify](../verify/SKILL.md) to select focused tests, affected live checks
   and reusable batch evidence. Update stale contracts/docs with the code.
   Run `task_lane.py review --task <id> --host <host>` for independent candidate
   review. It supplies the bounded procedure packet directly to the reviewer.
   Resolve its findings within the flow's correction budget.
5. Complete the authorized PR and merge path. On failure, diagnose the actual
   blocker, make a bounded repair and preserve evidence. Feed recurring process
   defects into the retrospective without expanding the active task silently.

## Evidence and completion

Report delivered tasks, proof and review results, PR/merge state and remaining
limitations. A source receipt records supplied text and its scope. Completion
requires the flow's checked outcomes. Avoid repeated checks on unchanged valid candidates.

## Conditional references

- For task branches and batch coordination, read [tasks and lanes](references/fast-lane.md).
- When `pr_proof` is `local_record`, read [local proof](references/local-proof.md).
