---
name: business-analysis
description: Business analysis knowledge for the team's analyst roles. Loaded by software-engineering-team flows; not user-facing.
exposure: internal
---

# Business Analysis

The project's analysis lives as tracked Markdown under
`workspace/docs/business-analysis/<topic>/`: goals, actors, processes, rules,
acceptance criteria, assumptions, open questions, decisions and measurable
non-functional budgets. `data/space-schema.json` declares its document types.

## When to Use

- A task adds or changes goals, actors, rules, acceptance criteria or
  non-functional budgets of a product topic.
- A build task's pre-check compares acceptance criteria with the analysis.

## Rules

An update to these documents is a knowledge task of the `deliver` entry and
runs the fast lane: branch, change, pull request, review, merge.

- `business-analyst` writes. Keep one living tree per topic and preserve
  stable IDs; retire an obsolete row instead of renumbering or reusing it.
- Put every fact in its owning document and link it instead of copying it.
- Cover empty, boundary, wrong-role, stale and concurrent cases, and quantify
  each non-functional budget.
- `analysis-challenger` reviews read-only and returns findings; the analyst
  resolves each blocking one in its owning document or records it as an
  assumption, open question or decision.
