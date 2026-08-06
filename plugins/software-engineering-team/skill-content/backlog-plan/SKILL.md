---
name: backlog-plan
description: User-gated baseline or replan entry that compiles an approved program and release backlog from analysis, solution, budget and exact Experience Design revisions, atomically applies it to PMO, then stops before delivery activation.
exposure: entry
---

# Backlog Plan

Create the durable delivery plan after the upstream product decisions are approved.

## When to Use

- A greenfield program has approved analysis, solution, design-system and experience baselines.
- An approved program needs a controlled replan outside active-lane contracts.

## Procedure

1. Run `preparation_check.py status --project-root <root> --json`. Require the response to name `backlog-plan`; an earlier entry is a hard route, not advice.
2. Load `flows/backlog-planning.md` in baseline mode and load `product-planning` for the product-owner.
3. Require the mechanical compiler, backlog reviewer, domain gates and program gate before PMO mutation.
4. Apply only through `backlog-plan apply` using the exact approved plan hash and gate revision.
5. Stop after reporting the applied program and releases. Explicitly state that backlog approval is not delivery activation. The user starts `deliver` or `delivery-lanes` separately.
