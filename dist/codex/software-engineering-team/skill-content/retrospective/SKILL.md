---
name: retrospective
description: Measure completed work and recurring failures, identify causal bottlenecks, and propose verified improvements without blocking delivery.
exposure: entry
---

# Retrospective

## Outcome

A small evidence-based improvement backlog ranked by time saved, risk reduced and cost.

## When to use

Local observation runs automatically after completion, command failure and native
session events. Read its grouped results for a requested retrospective or an active
loop review. Use deeper analysis when the evidence identifies a material loss.

## Inputs and authority

Read project receipts and available host usage records. The bounded background
observer writes local reports without a model call, test run or remote request.
Opaque shell outcomes share a delayed refresh. Explicit failures and lifecycle
events request immediate observation.
Project evidence stays local. Fixes and publication retain their own authority.

## Method

1. Read `retrospective.py report --project <root>` for the compact saved report.
   Add `--json` when inspecting named failures, source delivery or missing usage.
   Check observation freshness, failures and missing evidence.
   For deeper PR history, run `task_lane.py report --recent <count> --rescan --json`
   in the existing checkout. Use `--no-sessions` when usage is unnecessary.
2. Separate active work, queueing, repeated context, proof, review and owner waits.
   Measure batch makespan as well as each task's request-to-ready time. Mark gaps
   and estimates, including unavailable prices and ambiguous session attribution.
3. For each material loss, identify the observation, likely cause, missed signal
   and smallest decisive probe. Include clean examples when judging false alarms.
4. Prefer one class-level repair over repeated symptom fixes. Estimate expected
   benefit and verification cost. Keep the quality/security floor and compare
   against the previous workflow on the same kinds of work.
5. Under project issue-publication authority, use `file-issues --ids <ids>` for
   accepted project proposals. Plugin defects enter issue-report's local repair
   path. Deduplicate before filing. The resolver claims only authorized issues.

## Evidence and completion

Return the largest bottleneck, concrete evidence, proposed fix and success measure.
Separate method loads, visible source delivery, reported application and reviewed
coverage. Keep unknown usage unknown and preserve failed attempts. Session-end
and later startup observations reconcile late usage. A missing hook or interrupted
observer leaves visible pending work for recovery.

## Conditional references

- Read [rework classification](references/rework.md) for recurring repair causes.
